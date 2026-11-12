# PC를 꺼도 실행하는 무료 종가 보고서

## GitHub 자체 예약

[Close Watch 웹사이트](https://close-watch-list.hansharedid.chatgpt.site/)에서 종목을 관리합니다. GitHub Actions가 화요일~토요일 한국 시간 오전 7시에 사이트의 최신 목록을 직접 읽고 전체 종목 메일을 보냅니다. ChatGPT 예약 작업이나 켜 둔 PC가 필요하지 않습니다.

오전 7시 20분 누락 재확인과 한국 날짜별 중복 방지를 사용합니다. 사이트 인증·조회가 실패하면 과거 목록으로 발송하지 않습니다. 등록된 종목 또는 도달 종목이 없어도 보고서를 한 통 보냅니다.

필요한 GitHub Secret은 기존 SMTP_PASSWORD와 비공개 사이트 접근용 SITE_SERVICE_TOKEN입니다. [설정 안내](GITHUB_ACTIONS.md), [실행 구조](SITE_BRIDGE.md)를 참고하세요.

GitHub 예약은 대기열 사정에 따라 지연될 수 있습니다. GitHub의 무료 사용 정책이 적용됩니다. [공식 예약 안내](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## Google Sheets 또는 PC 버전

[Google Apps Script](../apps_script/README.md)는 기본 30분마다 Google에서 실행합니다. [PC 버전](../README.md#windows에서-시작하기)은 켜 둔 컴퓨터에서 동작합니다. 이들 버전의 조건·기록·메일은 GitHub 예약과 별개입니다.
