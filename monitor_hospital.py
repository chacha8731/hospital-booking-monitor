import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service

# 설정
BOOKING_URL = "https://booking.naver.com/booking/13/bizes/448698/items/3707473?from=myp&startDate=2026-08-07"
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
STATE_FILE = "booking_state.json"
CONFIG_FILE = "config.json"

def load_config():
    """설정 파일 로드"""
    if Path(CONFIG_FILE).exists():
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "target_date_start": None,
        "target_date_end": None,
        "target_weekdays": "Mon-Fri",
        "target_times": "09:00-12:00"
    }

def is_target_date(date_str, config):
    """대상 날짜인지 확인"""
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    # 날짜 범위 확인
    if config.get("target_date_start"):
        start_date = datetime.strptime(config["target_date_start"], "%Y-%m-%d").date()
        if target_date < start_date:
            return False

    if config.get("target_date_end"):
        end_date = datetime.strptime(config["target_date_end"], "%Y-%m-%d").date()
        if target_date > end_date:
            return False

    # 요일 확인
    weekday_map = {
        0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"
    }
    current_weekday = weekday_map[target_date.weekday()]

    target_weekdays = config.get("target_weekdays", "Mon-Fri")
    if "-" in target_weekdays:  # "Mon-Fri" 형식
        start_day, end_day = target_weekdays.split("-")
        weekday_list = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        start_idx = weekday_list.index(start_day)
        end_idx = weekday_list.index(end_day)
        allowed_days = weekday_list[start_idx:end_idx+1]
        if current_weekday not in allowed_days:
            return False
    elif "," in target_weekdays:  # "Mon,Wed,Fri" 형식
        allowed_days = [d.strip() for d in target_weekdays.split(",")]
        if current_weekday not in allowed_days:
            return False

    return True

def is_target_time(time_str, config):
    """대상 시간인지 확인"""
    try:
        time_obj = datetime.strptime(time_str.strip(), "%H:%M").time()
    except:
        return False

    target_times = config.get("target_times", "09:00-12:00")
    for time_range in target_times.split(","):
        time_range = time_range.strip()
        if "-" in time_range:
            start_str, end_str = time_range.split("-")
            start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
            end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
            if start_time <= time_obj <= end_time:
                return True

    return False

def load_previous_state():
    """이전 상태 로드"""
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_state(state):
    """현재 상태 저장"""
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_slack_message(title, available_slots):
    """Slack 메시지 전송"""
    if not SLACK_WEBHOOK_URL:
        print("❌ Slack Webhook URL이 설정되지 않았습니다.")
        return

    message = {
        "text": f"🎉 {title}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🏥 병원 예약 빈자리 발생!*\n\n{available_slots}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                }
            }
        ]
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=message)
        if response.status_code == 200:
            print("✅ Slack 알림 전송됨")
        else:
            print(f"❌ Slack 전송 실패: {response.status_code}")
    except Exception as e:
        print(f"❌ Slack 전송 오류: {e}")

def get_available_slots(driver, config, check_days=30):
    """네이버 예약에서 빈자리 정보 추출 (필터링 적용)"""
    available = {}

    try:
        # 페이지 로드 대기
        wait = WebDriverWait(driver, 10)

        # 달력 컨테이너 찾기
        calendar = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "calendar")))

        # 향후 N일의 날짜 확인
        for day_offset in range(check_days):
            target_date = datetime.now() + timedelta(days=day_offset)
            date_str = target_date.strftime("%Y-%m-%d")

            # 대상 날짜인지 확인
            if not is_target_date(date_str, config):
                continue

            try:
                # 날짜 버튼 클릭
                date_button = driver.find_element(
                    By.XPATH,
                    f"//button[contains(@aria-label, '{target_date.strftime('%m월 %d일')}')]"
                )

                if "disabled" not in date_button.get_attribute("class"):
                    date_button.click()
                    time.sleep(1)

                    # 시간대 추출 (검정색 = 가능)
                    time_slots = driver.find_elements(
                        By.CSS_SELECTOR,
                        ".time-slot:not(.disabled)"  # 활성화된 시간만
                    )

                    if time_slots:
                        # 대상 시간만 필터링
                        filtered_times = [
                            slot.text for slot in time_slots
                            if is_target_time(slot.text, config)
                        ]

                        if filtered_times:
                            available[date_str] = filtered_times
                            print(f"✅ {date_str}: {len(filtered_times)}개 시간대 가능")
                        else:
                            print(f"⏭️  {date_str}: 대상 시간대 없음")

            except Exception as e:
                print(f"⚠️ {date_str} 확인 중 오류: {e}")
                continue

        return available

    except Exception as e:
        print(f"❌ 빈자리 추출 오류: {e}")
        return {}

def main():
    print("🏥 병원 예약 모니터링 시작...")

    # 설정 로드
    config = load_config()
    print(f"📋 설정 로드:")
    print(f"   • 날짜: {config.get('target_date_start')} ~ {config.get('target_date_end')}")
    print(f"   • 요일: {config.get('target_weekdays')}")
    print(f"   • 시간: {config.get('target_times')}")

    # Chrome 옵션
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")

    try:
        driver = webdriver.Chrome(
            service=Service('/usr/bin/chromedriver'),
            options=options
        )
    except:
        driver = webdriver.Chrome(options=options)

    try:
        driver.get(BOOKING_URL)
        time.sleep(3)  # 페이지 로드 대기

        # 빈자리 정보 추출 (필터링 적용)
        current_available = get_available_slots(driver, config)

        if not current_available:
            print("⚠️ 현재 대상 조건의 예약가능한 시간이 없습니다.")
            return

        # 이전 상태와 비교
        previous_available = load_previous_state()

        # 새로운 빈자리 발견
        new_slots = {}
        for date, times in current_available.items():
            if date not in previous_available:
                new_slots[date] = times
            elif set(times) != set(previous_available.get(date, [])):
                new_slots[date] = times

        if new_slots:
            slot_text = "\n".join([
                f"📅 {date}: {', '.join(times)}"
                for date, times in new_slots.items()
            ])
            send_slack_message("새로운 예약 시간대가 열렸습니다!", slot_text)
        else:
            print("📊 변화 없음")

        # 상태 저장
        save_state(current_available)

    finally:
        driver.quit()
        print("✅ 모니터링 완료")

if __name__ == "__main__":
    main()
