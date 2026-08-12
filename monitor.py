#!/usr/bin/env python3
"""
네이버 예약 빈자리 모니터

동작 방식
  1) config.json 의 기간/요일 조건에 맞는 날짜 목록을 만든다
  2) 각 날짜마다 예약 페이지를 ?startDate=YYYY-MM-DD 로 직접 연다 (여러 날짜를 동시에 열어 속도를 낸다)
  3) 페이지 안에서 "13:30" 또는 "1:30"(+ 별도의 "오전"/"오후" 제목) 형태의
     시간 텍스트를 가진 요소를 전부 수집한다. 12시간 형식인 업체는 문서 순서상
     직전에 지나친 "오전"/"오후" 제목을 붙여 24시간제로 환산한다
  4) 비활성(disabled 속성 / 마감 계열 클래스)인지, 아니면 글자색이 회색인지로
     예약 가능 여부를 판별한다  (검정 = 가능, 회색 = 마감)
  5) 이전 실행 결과(booking_state.json)와 비교해 새로 생긴 자리만 Slack 알림

CSS 선택자를 지정하지 않으므로 네이버가 클래스명을 바꿔도 동작한다.
실행할 때마다 debug/ 폴더에 스크린샷과 수집 원본을 남기므로,
결과가 이상하면 그 파일만 보면 원인을 알 수 있다.
"""

import asyncio
import json
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "booking_state.json"
DEBUG_DIR = ROOT / "debug"

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
# 수동 실행(workflow_dispatch)일 때는 빈자리가 없어도 결과 요약을 Slack 으로 보낸다.
# -> 알림 배관이 살아있는지 사용자가 눈으로 확인할 수 있게 하기 위함
ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "0") == "1"

# 같은 브라우저 안에서 동시에 열어 둘 날짜 페이지 수.
# 하나씩 순서대로 열면 (날짜 수 x 업체 수) 만큼 그대로 시간이 누적되므로,
# 페이지를 여러 개 동시에 띄워 전체 실행 시간을 줄인다.
CONCURRENCY = int(os.environ.get("MONITOR_CONCURRENCY", "6"))

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TIME_TEXT = re.compile(r"^(\d{1,2}):(\d{2})$")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 페이지 안에서 실행되는 수집 스크립트.
# 시간 형태 텍스트를 가진 "가장 안쪽" 요소만 골라, 클릭 가능한 조상과 함께 상태를 읽는다.
# 네이버 예약은 업체마다 "13:30"(24시간) 또는 "1:30"+"오전/오후"(12시간, 구간 제목 분리)
# 두 형식을 섞어 쓰므로, 문서 순서대로 훑으며 "오전/오후" 제목을 지나칠 때마다
# 이후 시간에 그 구간(period)을 붙여준다.
JS_COLLECT = r"""
() => {
  const TIME = /^\d{1,2}:\d{2}$/;
  const PERIOD = /^(오전|오후|AM|PM)$/i;
  const out = [];
  const seen = new Set();
  let period = null;

  const nodes = document.querySelectorAll(
    'button, a, li, td, div, span, label, p, strong, em, h1, h2, h3, h4, h5, h6'
  );

  for (const el of nodes) {
    // 이 요소 자신의 텍스트만 본다 (자식 텍스트는 제외) -> "오전/오후" 제목 판별용
    let ownText = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) ownText += n.textContent;
    }
    ownText = ownText.trim();
    if (PERIOD.test(ownText)) {
      period = /오후|pm/i.test(ownText) ? 'PM' : 'AM';
      continue;
    }

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
      period: period,
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


def normalize_time(text, period=None, window=None):
    """'4:00' + period='PM' -> '16:00'. period가 없으면 감시 시간대(window)에
    맞춰 오전/오후 중 그럴듯한 쪽을 고른다. 그래도 못 정하면 원문 그대로 둔다."""
    m = TIME_TEXT.match(text)
    if not m:
        return None
    hour, minute = int(m.group(1)), m.group(2)
    if hour > 23:
        return None

    if hour <= 12 and period in ("AM", "PM"):
        if period == "PM" and hour != 12:
            hour += 12
        elif period == "AM" and hour == 12:
            hour = 0
    elif hour <= 12 and window:
        lo_h = int(window[0].split(":")[0])
        hi_h = int(window[1].split(":")[0])
        pm_hour = hour if hour == 12 else hour + 12
        as_is_in_window = lo_h <= hour <= hi_h
        pm_in_window = lo_h <= pm_hour <= hi_h
        if pm_in_window and not as_is_in_window:
            hour = pm_hour

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


async def scan_date(context, base_url, day, cfg, take_screenshot_to=None):
    """하루치 페이지를 새 탭에서 열어 예약 가능한 시간 목록을 반환.
    페이지를 이 함수 안에서 열고 닫으므로, 여러 날짜를 동시에 돌려도 서로 간섭하지 않는다."""
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}startDate={day.isoformat()}"

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)

        # 시간 텍스트가 하나라도 그려질 때까지 대기 (없으면 그대로 진행)
        try:
            await page.wait_for_function(
                "() => [...document.querySelectorAll('button, a, li, td, div, span')]"
                ".some(e => /^\\d{1,2}:\\d{2}$/.test((e.innerText || '').trim()))",
                timeout=10_000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(800)

        raw = await page.evaluate(JS_COLLECT)

        if take_screenshot_to:
            await page.screenshot(path=str(take_screenshot_to) + ".png", full_page=True)
            (Path(str(take_screenshot_to) + ".html")).write_text(
                await page.content(), encoding="utf-8"
            )
    finally:
        await page.close()

    available, mode = split_available(raw)

    lo, hi = cfg.get("time_start", "00:00"), cfg.get("time_end", "23:59")
    times = sorted(
        {
            t
            for t in (
                normalize_time(s["time"], s.get("period"), (lo, hi)) for s in available
            )
            if t and lo <= t <= hi
        }
    )

    entry = {
        "date": day.isoformat(),
        "url": url,
        "collected": len(raw),
        "mode": mode,
        "available_in_window": times,
        "sample": raw[:12],
    }
    log(f"  {day} | 수집 {len(raw):>3}개 | 판별 {mode:<9} | 조건내 가능 {len(times)}개 {times}")
    return times, entry


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


def notify_new_slots(name, new_slots, booking_url):
    lines = [
        f"*{d}* ({WEEKDAYS[date.fromisoformat(d).weekday()]})  {', '.join(times)}"
        for d, times in sorted(new_slots.items())
    ]
    body = "\n".join(lines)
    return send_slack(
        f"🏥 [{name}] 예약 빈자리 {sum(len(v) for v in new_slots.values())}건 발견",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🏥 [{name}] 빈자리가 났습니다!*\n\n{body}",
                },
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


def safe_filename(text):
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", text).strip("_") or "target"


async def run_target(context, target, previous_state, semaphore):
    name = target.get("name") or target["booking_url"]
    booking_url = target["booking_url"]
    days = target_dates(target)

    log("-" * 60)
    log(f"[{name}]")
    log(f"  기간   : {target['date_start']} ~ {target['date_end']}")
    log(f"  시간   : {target.get('time_start')} ~ {target.get('time_end')}")
    log(f"  요일   : {', '.join(target.get('weekdays', WEEKDAYS))}")
    log(f"  대상일 : {len(days)}일 (동시 {CONCURRENCY}개씩 확인)")

    debug = {"name": name, "dates": []}

    if not days:
        log("  확인할 날짜가 없습니다. (기간이 이미 지났는지 확인하세요)")
        return name, {}, 0, {}, debug

    stem = safe_filename(name)

    async def bound_scan(day, is_first):
        async with semaphore:
            shot = DEBUG_DIR / f"{stem}_first_date" if is_first else None
            try:
                return day, await scan_date(context, booking_url, day, target, shot)
            except Exception as e:
                log(f"  {day} 확인 실패: {e}")
                return day, (None, {"date": day.isoformat(), "error": str(e)})

    results = await asyncio.gather(
        *(bound_scan(day, i == 0) for i, day in enumerate(days))
    )

    found = {}
    for day, (times, entry) in sorted(results, key=lambda r: r[0]):
        debug["dates"].append(entry)
        if times:
            found[day.isoformat()] = times

    total_collected = sum(d.get("collected", 0) for d in debug["dates"])
    previous = previous_state.get(name, {})
    new_slots = {}
    for day_str, times in found.items():
        fresh = sorted(set(times) - set(previous.get(day_str, [])))
        if fresh:
            new_slots[day_str] = fresh

    log(
        f"  수집 {total_collected}개 | 빈자리 {sum(len(v) for v in found.values())}건 "
        f"| 신규 {sum(len(v) for v in new_slots.values())}건"
    )

    if new_slots:
        notify_new_slots(name, new_slots, booking_url)

    return name, found, total_collected, new_slots, debug


async def main_async():
    DEBUG_DIR.mkdir(exist_ok=True)
    cfg = read_json(CONFIG_FILE, None)
    targets = (cfg or {}).get("targets") or []
    if not targets:
        log("config.json 에 targets 가 없습니다.")
        return 1

    log("=" * 60)
    log(f"네이버 예약 빈자리 모니터 — 감시 대상 {len(targets)}곳")
    log(f"Slack  : {'설정됨' if SLACK_WEBHOOK_URL else '미설정'}")
    log("=" * 60)

    previous_state = read_json(STATE_FILE, {})
    debug_all = []
    new_state = {}
    any_new = False
    total_collected_all = 0

    # 날짜 페이지 동시 개수를 이 세마포어로 제한한다 (한 브라우저 안에서 탭만 여러 개 연다).
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 1600},
        )

        for target in targets:
            name, found, collected, new_slots, debug = await run_target(
                context, target, previous_state, semaphore
            )
            new_state[name] = found
            total_collected_all += collected
            debug_all.append(debug)
            if new_slots:
                any_new = True

        await context.close()
        await browser.close()

    write_json(
        DEBUG_DIR / "scan.json",
        {"run_at": datetime.now().isoformat(timespec="seconds"), "targets": debug_all},
    )

    if not any_new and ALWAYS_NOTIFY:
        if total_collected_all == 0:
            msg = (
                "⚙️ *모니터 점검 실행*\n"
                f"감시 대상 {len(targets)}곳 모두 정상 실행됐지만 시간표를 찾지 못했습니다.\n"
                "지금 예약이 마감(닫힘) 상태라면 정상입니다."
            )
        else:
            msg = (
                "⚙️ *모니터 점검 실행*\n"
                f"감시 대상 {len(targets)}곳, 시간표 {total_collected_all}개를 읽었고 조건에 맞는 빈자리는 없습니다.\n"
                "감시는 정상 동작 중입니다."
            )
        send_slack(
            "⚙️ 모니터 점검 실행",
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": msg}}],
        )

    write_json(STATE_FILE, new_state)
    log("=" * 60)
    log("완료")
    return 0


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        # 실패해도 워크플로가 빨갛게 뜨지 않도록 0으로 종료 (원인은 로그/debug 로 확인)
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        sys.exit(0)
