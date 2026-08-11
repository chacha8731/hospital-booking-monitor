# 병원 예약 빈자리 모니터

네이버 예약 페이지를 주기적으로 확인해, 조건에 맞는 빈자리가 새로 생기면 Slack 으로 알려줍니다.

## 파일

| 파일 | 역할 |
|---|---|
| `monitor.py` | 예약 페이지를 열어 빈자리를 찾고 Slack 으로 알림 |
| `config.json` | 감시할 기간·요일·시간대 설정 |
| `requirements.txt` | 필요한 파이썬 패키지 |
| `booking_state.json` | 직전 실행 결과 (중복 알림 방지용, 자동 갱신) |
| `.github/workflows/monitor.yml` | GitHub Actions 자동 실행 설정 |

## 설정 바꾸기

`config.json` 만 고치면 됩니다.

```json
{
  "booking_url": "https://booking.naver.com/booking/13/bizes/448698/items/3707473",
  "date_start": "2026-08-11",
  "date_end":   "2026-08-31",
  "weekdays":   ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  "time_start": "13:00",
  "time_end":   "20:00",
  "max_dates_per_run": 25
}
```

- `weekdays` — 평일만 원하면 `["Mon","Tue","Wed","Thu","Fri"]`
- `time_start` / `time_end` — 이 사이에 있는 시간만 알림
- `max_dates_per_run` — 한 번에 확인할 최대 날짜 수 (많을수록 실행이 오래 걸림)

## 확인 주기 바꾸기

`.github/workflows/monitor.yml` 의 `cron` 값:

```yaml
- cron: '*/10 * * * *'   # 10분마다 (기본)
- cron: '*/5 * * * *'    # 5분마다
- cron: '0 * * * *'      # 1시간마다
```

GitHub 무료 cron 은 서버 부하에 따라 몇 분 늦게 실행될 수 있습니다. 정상입니다.

## 동작 확인 방법

**Actions → Hospital Booking Monitor → Run workflow** 로 수동 실행하면,
빈자리가 없어도 Slack 으로 점검 메시지를 보냅니다. 알림 연결이 살아있는지 확인용입니다.

## 결과가 이상할 때

실행할 때마다 `debug` 아티팩트가 생깁니다. Actions 실행 화면 맨 아래에서 받을 수 있고,
안에 아래 파일이 들어 있습니다.

- `first_date.png` — 실제로 열린 화면 (페이지가 제대로 떴는지)
- `first_date.html` — 그때의 HTML 전체
- `scan.json` — 날짜별로 몇 개를 읽었고 어떻게 판별했는지
- `error.txt` — 예외가 났을 경우의 상세 내용

## 사전 준비

GitHub 저장소 **Settings → Secrets and variables → Actions** 에
`SLACK_WEBHOOK_URL` 이름으로 Slack Incoming Webhook 주소를 등록해야 합니다.
