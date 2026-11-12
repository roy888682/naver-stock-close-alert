# 웹 목록과 GitHub 오전 7시 보고서

[웹사이트](https://close-watch-list.hansharedid.chatgpt.site/)에서 한국·미국 주식과 ETF의 티커, 목표가, 위아래 비율을 등록합니다. 종목명은 자동 조회하며 목록에서 수정·일시정지·삭제할 수 있습니다.

GitHub Actions가 **화요일~토요일 오전 7시, Asia/Seoul**에 직접 실행됩니다. 오전 7시 20분에 누락을 재확인하고 날짜별 발송 기록으로 중복을 막습니다. 실행 시마다 사이트의 최신 목록을 읽으므로 PC와 ChatGPT 예약 작업이 필요하지 않습니다. GitHub 대기열에 따라 실제 도착은 늦어질 수 있습니다.

목표 범위에 해당하는 종목이 없어도 모든 등록 종목을 한 표로 보냅니다. 도달 종목은 위에 모으고 종목명·상태를 빨간색으로 표시합니다. 종목명은 네이버증권으로 연결됩니다. 일시정지와 조회 실패도 표시합니다.

## 1. Gmail 발송 설정

이미 설정된 **SMTP_PASSWORD**는 그대로 사용합니다. 새 설치에서만 발신 Gmail의 앱 비밀번호를 GitHub **Settings → Secrets and variables → Actions**에 `SMTP_PASSWORD`라는 Secret으로 등록합니다. 값을 저장소나 채팅에 올리지 않습니다.

- 발신: royhan88888@gmail.com
- 수신: ykhan@dacpole.com
- SMTP: smtp.gmail.com / 465 / SSL/TLS
- 기존 GMAIL_APP_PASSWORD도 대체 값으로 지원

## 2. 비공개 사이트 연결

같은 GitHub Secret 설정에 **SITE_SERVICE_TOKEN**을 등록합니다. 이는 소유자 전용 사이트의 서비스 접근 키이며, GitHub가 그 사이트의 최신 목록을 읽는 용도입니다. 사이트 공개 설정은 바꾸지 않습니다. 키는 소스·로그·상태 DB에 기록하지 않습니다.

조회나 인증에 실패하면 과거 저장소 목록으로 대신 보내지 않고 실행을 실패 처리합니다. 실패 로그에 SITE_SERVICE_TOKEN이 나오면 Secret 등록·값을 확인합니다.

## 3. 연결 검증과 메일 테스트

[Actions 실행 화면](https://github.com/roy888682/naver-stock-close-alert/actions/workflows/daily-close-alert.yml)에서 **Run workflow → main**을 선택합니다.

1. `check-only`: 사이트 최신 목록 조회와 종가를 확인합니다. 메일 없음.
2. `check`: 오늘 정기 목록 메일을 실제로 보냅니다. 오늘 이미 보냈으면 생략합니다.
3. `test-digest`: 별도 테스트 제목으로 전체 목록을 보냅니다. 테스트는 정기 발송 기록과 분리됩니다.

세부 실행 구조와 중복 방지는 [SITE_BRIDGE.md](SITE_BRIDGE.md)를 참고하세요. 과거 날짜 요청 파일과 ChatGPT 예약 작업은 더 이상 사용하지 않습니다.

## 기록과 오류

`alert-state` 브랜치의 alerts.db가 발송 상태를 보관합니다. pending/uncertain은 메일 접수 여부가 불명확하므로 자동 재발송하지 않습니다. 받은 편지함·보낸 편지함과 해당 실행을 확인하고 기록을 임의 삭제하지 않습니다.

공개 저장소의 상태 DB에는 종목·목표가·발송 기록이 보일 수 있습니다. SMTP 비밀번호와 사이트 접근 키는 GitHub Secret에만 유지됩니다.

기존 PC용과 [Google Apps Script용](../apps_script/README.md)은 별도의 조건·발송 일정을 사용합니다.
