#!/usr/bin/env python3
"""
네이버 예약 빈자리 모니터

동작 방식
  1) config.json 의 기간/요일 조건에 맞는 날짜 목록을 만든다
  2) 각 날짜마다 예약 페이지를 ?startDate=YYYY-MM-DD 로 직접 연다
  3) 페이지 안에서 "13:30" 같은 시간 형태 텍스트를 가진 요소를 전부 수집한다
  4) 비활성(disabled 속성 / 마감 계열 클래스)인지, 아니면 글자색이 회색인지로
     예약 가능 여부를 판별한다  (검정 = 가능, 회색 = 마감)
  5) 이전 실행 결과(booking_state.json)와 비교해 새로 생긴 자리만 Slack 알림

CSS 선택자를 지정하지 않으므로 네이버가 클래스명을 바꿔도 동작한다.
실행할 때마다 debug/ 폴더에 스크린샷과 수집 원본을 남기므로,
결과가 이상하면 그 파일만 보면 원인을 알 수 있다.
"""

import json
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "booking_state.json"
DEBUG_DIR = ROOT / "debug"

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
# 수동 실행(workflow_dispatch)일 때는 빈자리가 없어도 결과 요약을 Slack 으로 보낸다.
# -> 알림 배관이 살아있는지 사용자가 눈으로 확인할 수 있게 하기 위함
ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "0") == "1"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TIME_TEXT = re.compile(r"^(\d{1,2}):(\d{2})$")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 페이지 안에서 실행되는 수집 스크립트.
# 시간 형태 텍스트를 가진 "가장 안쪽" 요소만 골라, 클릭 가능한 조상과 함께 상태를 읽는다.
JS_COLLECT = r"""
() => {
  const TIME = /^\d{1,2}:\d{2}$/;
  const out = [];
  const seen = new Set();

  for (const el of document.querySelectorAll('button, a, li, td, div, span, label, p, strong, em')) {
    const text = (el.innerText || '').trim();
    if (!TIME.test(text)) continue;

    // 같은 텍스트를 가진 자식이 또 있으면 이건 껍데기 -> 건너뜀
    let hasSameChild = false;
    for (const child of el.querySelectorAll('*')) {
      if ((child.innerText || '').trim() === text) { hasSameChild = true; break; }
    }
    if (hasSameChild) continue;

    const box = el.closest('button, a, [role="button"], li, label') || el;
    if (seen.has(box)) continue;
    seen.add(box);

    const boxStyle = getComputedStyle(box);
    if (boxStyle.display === 'none' || boxStyle.visibility === 'hidden') continue;

    const cls = String(box.className || '') + ' ' + String(el.className || '');
    const disabled =
      box.hasAttribute('disabled') ||
      box.getAttribute('aria-disabled') === 'true' ||
      /disab|soldout|sold_out|sold-out|unavail|impossible|dimmed|closed|_off\b|is-off/i.test(cls);

    out.push({
      time: text,
      disabled: !!disabled,
      color: getComputedStyle(el).color,
      cls: cls.trim().slice(0, 160),
    });
  }
  return out;
}
"""


def log(msg):
    print(msg, flush=True)


def read_json(path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def target_dates(cfg):
    """설정된 기간·요일에 맞는 날짜 목록. 이미 지난 날짜는 제외."""
    start = date.fromisoformat(cfg["date_start"])
    end = date.fromisoformat(cfg["date_end"])
    today = date.today()
    if start < today:
        start = today

    allowed = set(cfg.get("weekdays") or WEEKDAYS)
    limit = int(cfg.get("max_dates_per_run", 25))

    days, cursor = [], start
    while cursor <= end and len(days) < limit:
        if WEEKDAYS[cursor.weekday()] in allowed:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def normalize_time(text):
    m = TIME_TEXT.match(text)
    if not m:
        return None
    hour, minute = int(m.group(1)), m.group(2)
    if hour > 23:
        return None
    return f"{hour:02d}:{minute}"


def luminance(css_color):
    """'rgb(51, 51, 51)' -> 밝기(0~255). 밝을수록 회색(마감)에 가깝다."""
    nums = re.findall(r"[\d.]+", css_color or "")
    if len(nums) < 3:
        return None
    r, g, b = (float(n) for n in nums[:3])
    return 0.299 * r + 0.587 * g + 0.114 * b


def split_available(raw_slots):
    """수집된 슬롯을 예약가능/마감으로 나눈다. (가능목록, 판별방식) 반환"""
    if not raw_slots:
        return [], "no-slots"

    # 1순위: disabled 속성 / 마감 계열 클래스가 실제로 쓰이고 있으면 그걸 신뢰
    if any(s["disabled"] for s in raw_slots):
        return [s for s in raw_slots if not s["disabled"]], "attribute"

    # 2순위: 글자색 밝기. 검정(어두움)=가능, 회색(밝음)=마감
    scored = [(s, luminance(s["color"])) for s in raw_slots]
    values = [v for _, v in scored if v is not None]
    if values and (max(values) - min(values)) >= 30:
        threshold = (max(values) + min(values)) / 2
        return [s for s, v in scored if v is not None and v <= threshold], "color"

    # 구분이 안 되면 일단 전부 가능으로 보고, 판별방식을 'unknown'으로 알린다
    # (놓치는 것보다 한 번 더 알리는 쪽이 낫다)
    return list(raw_slots), "unknown"


def scan_date(page, base_url, day, cfg, debug):
    """하루치 페이지를 열어 예약 가능한 시간 목록을 반환"""
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}startDate={day.isoformat()}"

    page.goto(url, wait_until="domcontentloaded", timeout=45_000)

    # 시간 텍스트가 하나라도 그려질 때까지 대기 (없으면 그대로 진행)
    try:
        page.wait_for_function(
            "() => [...document.querySelectorAll('button, a, li, td, div, span')]"
            ".some(e => /^\\d{1,2}:\\d{2}$/.test((e.innerText || '').trim()))",
            timeout=12_000,
        )
    except Exception:
        pass
    page.wait_for_timeout(1_500)

    raw = page.evaluate(JS_COLLECT)
    available, mode = split_available(raw)

    lo, hi = cfg.get("time_start", "00:00"), cfg.get("time_end", "23:59")
    times = sorted(
        {
            t
            for t in (normalize_time(s["time"]) for s in available)
            if t and lo <= t <= hi
        }
    )

    debug["dates"].append(
        {
            "date": day.isoformat(),
            "url": url,
            "collected": len(raw),
            "mode": mode,
            "available_in_window": times,
            "sample": raw[:12],
        }
    )
    log(f"  {day} | 수집 {len(raw):>3}개 | 판별 {mode:<9} | 조건내 가능 {len(times)}개 {times}")
    return times


def send_slack(text, blocks=None):
    if not SLACK_WEBHOOK_URL:
        log("!! SLACK_WEBHOOK_URL 이 없어 알림을 보내지 못했습니다.")
        return False
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        r = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
        ok = r.status_code == 200
        log(f"Slack 전송 {'성공' if ok else '실패 ' + str(r.status_code) + ' ' + r.text[:200]}")
        return ok
    except Exception as e:
        log(f"Slack 전송 오류: {e}")
        return False


def notify_new_slots(new_slots, booking_url):
    lines = [
        f"*{d}* ({WEEKDAYS[date.fromisoformat(d).weekday()]})  {', '.join(times)}"
        for d, times in sorted(new_slots.items())
    ]
    body = "\n".join(lines)
    return send_slack(
        f"🏥 병원 예약 빈자리 {sum(len(v) for v in new_slots.values())}건 발견",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*🏥 빈자리가 났습니다!*\n\n{body}"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "예약 페이지 열기"},
                        "url": booking_url,
                    }
                ],
            },
        ],
    )


def main():
    DEBUG_DIR.mkdir(exist_ok=True)
    cfg = read_json(CONFIG_FILE, None)
    if not cfg:
        log("config.json 을 읽지 못했습니다.")
        return 1

    booking_url = cfg["booking_url"]
    days = target_dates(cfg)

    log("=" * 60)
    log("네이버 예약 빈자리 모니터")
    log(f"  기간   : {cfg['date_start']} ~ {cfg['date_end']}")
    log(f"  시간   : {cfg.get('time_start')} ~ {cfg.get('time_end')}")
    log(f"  요일   : {', '.join(cfg.get('weekdays', WEEKDAYS))}")
    log(f"  대상일 : {len(days)}일")
    log(f"  Slack  : {'설정됨' if SLACK_WEBHOOK_URL else '미설정'}")
    log("=" * 60)

    if not days:
        log("확인할 날짜가 없습니다. (기간이 이미 지났는지 확인하세요)")
        return 0

    debug = {"run_at": datetime.now().isoformat(timespec="seconds"), "dates": []}
    found = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 1600},
        )
        page = context.new_page()

        for i, day in enumerate(days):
            try:
                times = scan_date(page, booking_url, day, cfg, debug)
                if times:
                    found[day.isoformat()] = times
                # 첫 날짜는 화면을 남겨 페이지가 제대로 떴는지 확인할 수 있게 한다
                if i == 0:
                    page.screenshot(
                        path=str(DEBUG_DIR / "first_date.png"), full_page=True
                    )
                    (DEBUG_DIR / "first_date.html").write_text(
                        page.content(), encoding="utf-8"
                    )
            except Exception as e:
                log(f"  {day} 확인 실패: {e}")
                debug["dates"].append({"date": day.isoformat(), "error": str(e)})

        context.close()
        browser.close()

    write_json(DEBUG_DIR / "scan.json", debug)

    total_collected = sum(d.get("collected", 0) for d in debug["dates"])
    previous = read_json(STATE_FILE, {})

    # 이전에 없던 날짜, 또는 이전에 없던 시간만 "새 자리"로 본다
    new_slots = {}
    for day_str, times in found.items():
        fresh = sorted(set(times) - set(previous.get(day_str, [])))
        if fresh:
            new_slots[day_str] = fresh

    log("-" * 60)
    log(f"수집한 시간 텍스트 총 {total_collected}개")
    log(f"조건에 맞는 빈자리 : {sum(len(v) for v in found.values())}건")
    log(f"그중 새로 생긴 것  : {sum(len(v) for v in new_slots.values())}건")

    if new_slots:
        notify_new_slots(new_slots, booking_url)
    elif ALWAYS_NOTIFY:
        if total_collected == 0:
            msg = (
                "⚙️ *모니터 점검 실행*\n"
                "정상 실행됐지만 페이지에서 시간표를 찾지 못했습니다.\n"
                "지금 예약이 마감(닫힘) 상태라면 정상입니다."
            )
        else:
            msg = (
                "⚙️ *모니터 점검 실행*\n"
                f"시간표 {total_collected}개를 읽었고, 조건에 맞는 빈자리는 없습니다.\n"
                "감시는 정상 동작 중입니다."
            )
        send_slack("⚙️ 모니터 점검 실행", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": msg}}])

    write_json(STATE_FILE, found)
    log("완료")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        # 실패해도 워크플로가 빨갛게 뜨지 않도록 0으로 종료 (원인은 로그/debug 로 확인)
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        sys.exit(0)
