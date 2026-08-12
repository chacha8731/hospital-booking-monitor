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

`config.json` 의 `targets` 배열에 병원을 하나씩 추가합니다. 여러 곳을 동시에 감시할 수 있습니다.

```json
{
  "targets": [
    {
      "name": "병원1",
      "booking_url": "https://booking.naver.com/booking/13/bizes/448698/items/3707473",
      "date_start": "2026-08-11",
      "date_end":   "2026-08-31",
      "weekdays":   ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
      "time_start": "13:00",
      "time_end":   "20:00",
      "max_dates_per_run": 25
    },
    {
      "name": "병원2",
      "booking_url": "https://booking.naver.com/booking/13/bizes/1163916/items/5909793",
      "date_start": "2026-08-11",
      "date_end":   "2026-08-31",
      "weekdays":   ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
      "time_start": "13:00",
      "time_end":   "20:00",
      "max_dates_per_run": 25
    }
  ]
}
```

- `name` — Slack 알림에 표시될 이름 (병원 구분용, 원하는 대로 변경 가능)
- `weekdays` — 평일만 원하면 `["Mon","Tue","Wed","Thu","Fri"]`
- `time_start` / `time_end` — 이 사이에 있는 시간만 알림
- `max_dates_per_run` — 한 번에 확인할 최대 날짜 수 (많을수록 실행이 오래 걸림)
- 병원을 더 추가하려면 `targets` 배열에 객체를 하나 더 넣으면 됩니다

## 확인 주기 바꾸기

`.github/workflows/monitor.yml` 의 `cron` 값:

```yaml
- cron: '*/20 * * * *'   # 20분마다 (기본)
- cron: '*/10 * * * *'   # 10분마다
- cron: '0 * * * *'      # 1시간마다
```

GitHub 무료 cron 은 서버 부하에 따라 실제로는 설정보다 늦게, 심하면 몇 시간 간격으로
실행될 수 있습니다. 짧은 간격(5~10분)일수록 이 지연이 심해지는 것이 GitHub 쪽 알려진
한계라, 너무 짧게 잡아도 체감상 더 자주 도는 게 아닐 수 있습니다.

한 번 실행에 걸리는 시간도 간격과 맞물립니다. `monitor.py`는 날짜 페이지를 한 번에
`MONITOR_CONCURRENCY`개(기본 6개)씩 동시에 열어 확인합니다. 감시 병원·날짜 수가 많으면
`.github/workflows/monitor.yml` 의 `Run monitor` 단계에 아래처럼 환경 변수를 추가해
늘릴 수 있습니다 (단, 너무 크게 잡으면 러너 자원 부족으로 오히려 느려지거나 실패할 수 있음).

```yaml
      - name: Run monitor
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
          ALWAYS_NOTIFY: ${{ github.event_name == 'workflow_dispatch' && '1' || '0' }}
          MONITOR_CONCURRENCY: '6'
        run: python monitor.py
```

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
