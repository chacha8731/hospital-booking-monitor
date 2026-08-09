# 🏥 병원 예약 모니터링 시스템 설정 가이드

## 1️⃣ Slack 웹훅 설정

### 1.1 Slack 워크스페이스 생성
- https://slack.com 접속 → 가입
- 워크스페이스 이름 입력 (예: "병원예약봇")

### 1.2 Slack 앱 설정
1. Slack 홈페이지 → **Apps** 클릭
2. **Create New App** → **From scratch**
3. App name: `Hospital Booking Bot`
4. 워크스페이스 선택
5. 좌측 메뉴 → **Incoming Webhooks** 클릭
6. **Add New Webhook to Workspace** 클릭
7. 채널 선택 (예: #general 또는 새 채널 #예약알림)
8. **Allow** 클릭

### 1.3 Webhook URL 복사
- Webhook URL 표시됨 (예: `https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX`)
- **이 URL을 저장해두세요** ← 다음 단계에서 필요!

---

## 2️⃣ GitHub 저장소 설정

### 2.1 GitHub 저장소 생성
1. https://github.com/new 접속
2. Repository name: `hospital-booking-monitor`
3. **Public** 선택 (GitHub Actions 무료 사용)
4. **Create repository**

### 2.2 파일 업로드
현재 폴더의 다음 파일들을 GitHub에 업로드:
```
hospital-booking-monitor/
├── monitor_hospital.py
├── requirements.txt
├── booking_state.json (빈 JSON 파일)
└── .github/
    └── workflows/
        └── monitor.yml
```

GitHub 웹에서:
- **Add file** → **Upload files**
- 파일들을 선택 및 업로드
- **Commit changes** 클릭

### 2.3 GitHub Secrets 설정
1. 저장소 → **Settings** 탭
2. 좌측 **Secrets and variables** → **Actions**
3. **New repository secret** 클릭
4. Name: `SLACK_WEBHOOK_URL`
5. Secret: 1.3에서 복사한 Webhook URL 붙여넣기
6. **Add secret**

---

## ⚙️ 모니터링 조건 설정

### config.json 수정
저장소에 `config.json` 파일이 있습니다. **필요에 따라 수정하세요:**

```json
{
  "target_date_start": "2026-08-15",
  "target_date_end": "2026-08-20",
  "target_weekdays": "Mon-Fri",
  "target_times": "09:00-12:00"
}
```

#### 설정값 설명

**1. 날짜 범위**
```
"target_date_start": "2026-08-15"  // 시작 날짜
"target_date_end": "2026-08-20"    // 종료 날짜
```
- 이 기간 내의 날짜만 확인
- 비워두면 모든 날짜 확인 (`null`)

**2. 요일 선택**
```
"target_weekdays": "Mon-Fri"       // 월~금만 (주말 제외)
"target_weekdays": "Mon,Wed,Fri"   // 월, 수, 금만
```
- 옵션: `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun`
- `Mon-Fri`: 월요일부터 금요일까지
- `Sat-Sun`: 토요일~일요일 (주말만)

**3. 시간대 선택**
```
"target_times": "09:00-12:00"      // 오전 (9시~12시)
"target_times": "14:00-18:00"      // 오후 (2시~6시)
"target_times": "09:00-12:00,14:00-18:00"  // 오전, 오후 둘 다
```

#### 예시

**예 1) 다음주 월~금 오전만**
```json
{
  "target_date_start": "2026-08-18",
  "target_date_end": "2026-08-22",
  "target_weekdays": "Mon-Fri",
  "target_times": "09:00-12:00"
}
```

**예 2) 8월 15~25일 모든 요일 모든 시간**
```json
{
  "target_date_start": "2026-08-15",
  "target_date_end": "2026-08-25",
  "target_weekdays": "Mon-Sun",
  "target_times": "00:00-23:59"
}
```

**예 3) 월/수/금 오전/오후 모두**
```json
{
  "target_date_start": null,
  "target_date_end": null,
  "target_weekdays": "Mon,Wed,Fri",
  "target_times": "09:00-12:00,14:00-18:00"
}
```

---

## 3️⃣ 모니터링 시작

### 3.1 자동 실행 확인
- 저장소 → **Actions** 탭
- "병원 예약 모니터링" 워크플로우 보임
- 상태 확인

### 3.2 수동 실행 (테스트)
- **Actions** → **병원 예약 모니터링** 선택
- **Run workflow** → **Run workflow** 클릭
- 30초 후 Slack에 알림이 오는지 확인

### 3.3 자동 실행 설정
- 위 설정 완료 후 **자동으로 5분마다 실행됨**
- GitHub Actions 탭에서 실행 이력 확인 가능

---

## 🔔 알림 테스트

1. GitHub에서 **Actions** 탭 접속
2. **병원 예약 모니터링** → **Run workflow** 클릭
3. 몇 초 후 Slack 채널에 메시지 도착 확인

---

## 📊 모니터링 상태 확인

### 실행 이력 확인
- **Actions** 탭에서 각 실행의 상세 로그 확인 가능
- ✅ 초록색: 성공
- ❌ 빨간색: 실패 (로그 확인 필요)

### 예약 상태 확인
- `booking_state.json` 파일에 마지막 확인 시점의 예약 정보 저장
- GitHub 웹에서 파일 열어보면 현재 상태 확인 가능

---

## ⚙️ 커스터마이징

### 모니터링 간격 변경
`.github/workflows/monitor.yml`에서:
```yaml
- cron: '*/5 * * * *'  # 5분마다 (*/5)
- cron: '*/10 * * * *'  # 10분마다 (*/10)
- cron: '0 * * * *'     # 1시간마다
```

### 확인 날짜 범위 변경
`monitor_hospital.py`에서:
```python
for day_offset in range(7):  # 7일 → 14일 등으로 변경
```

---

## 🆘 문제 해결

### Slack 알림이 안 옴
1. **Webhook URL 정확성** 확인: Settings → Secrets → `SLACK_WEBHOOK_URL`
2. **워크플로우 로그** 확인: Actions → 실행 선택 → 상세 로그
3. 수동 실행 시도: **Run workflow** 클릭

### "Page not found" 또는 로그인 필요
- 네이버 예약 페이지가 변경됐을 수 있음
- `monitor_hospital.py`의 CSS Selector 업데이트 필요
- (예약 가능/마감 표시 방식 변경 시)

### GitHub Actions 실행 안 됨
1. **Settings** → **Actions** → **General** 확인
2. "Allow all actions" 선택되어 있는지 확인
3. `.github/workflows/monitor.yml` 파일 존재 확인

---

## 💡 주의사항

- 🔒 Slack Webhook URL은 절대 코드에 직접 쓰지 마세요 (Secrets 사용)
- ⏰ GitHub Actions는 정확히 5분마다 실행되지만, 가끔 몇 초 지연될 수 있음
- 🌍 무료 GitHub Actions는 월 2,000분 무료 (2,000분 ÷ 5분 = 400회 실행 가능)
- 📱 Slack 모바일에서도 알림 받기 설정 권장

---

**완료되면 병원 예약이 오픈될 때 자동으로 알림을 받게 됩니다!** 🎉
