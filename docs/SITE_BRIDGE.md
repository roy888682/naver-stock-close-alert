# GitHub 자체 예약과 비공개 웹 목록 연결

[Close Watch](https://close-watch-list.hansharedid.chatgpt.site/)에서 종목을 관리하고 GitHub Actions가 직접 메일을 보냅니다. ChatGPT 예약 작업과 날짜 요청 파일에 의존하지 않습니다.

## 일정과 실행 경로

- 워크플로: `.github/workflows/daily-close-alert.yml`
- 기본 일정: `0 7 * * 2-6`, `timezone: Asia/Seoul` — 화요일~토요일 오전 7시
- 누락 재확인: 같은 요일 오전 7시 20분. 한국 날짜별 발송 기록으로 이미 접수된 메일은 생략합니다.
- GitHub 대기열 사정으로 실제 시작·도착은 늦어질 수 있습니다.
- 코드 push와 과거 `cloud-run-request.json` / `cloud-test-request.json` 변경은 이 메일 워크플로를 시작하지 않습니다.

매 실행은 비공개 사이트의 `/api/runner/watchlist`를 HTTPS로 읽고 전체 스키마를 검증합니다. 검증된 목록은 실행기 안의 `data/site-watchlist.json`에만 저장하고 이를 메일 실행기에 전달합니다. 사이트의 종목 추가·수정·삭제는 다음 실행부터 반영됩니다. 사이트 로그인 화면, 인증 실패, 형식 오류, 잘린 응답은 모두 발송을 중단합니다. 저장소에 남은 과거 `watchlist.json`으로 대체하지 않습니다. 정상적인 빈 목록은 삭제를 반영한 것으로 인정하여 '등록 종목 없음' 보고서를 보냅니다.

## 필요한 설정

[GitHub Actions Secret 설정](https://github.com/roy888682/naver-stock-close-alert/settings/secrets/actions)에 다음 두 값을 보관합니다.

| 이름 | 용도 |
| --- | --- |
| `SMTP_PASSWORD` | 기존 발신 Gmail 앱 비밀번호. 기존 값을 유지합니다. |
| `SITE_SERVICE_TOKEN` | 소유자 전용 Site의 서비스 접근 키. 해당 Site의 `OAI-Sites-Authorization` 헤더에만 사용합니다. |

키 값은 저장소, 상태 DB, 로그, 날짜 파일, 채팅 또는 사이트의 클라이언트 코드에 넣지 않습니다. 사이트를 공개로 바꾸지 않습니다. 서비스 키를 교체했다면 GitHub Secret도 갱신하고 미발송 확인부터 검증합니다.

## 전체 목록과 중복 방지

확정된 정규장 종가로 모든 등록 종목을 한 표에 넣습니다. 설정 범위 안 종목을 먼저 정렬하고 종목명과 '목표가 도달'을 빨간색으로 표시합니다. 종목명은 네이버증권 링크입니다. 목표 범위 미달·일시정지·조회 실패도 행을 유지합니다. 등록 종목이나 범위 도달 종목이 없어도 한 통 발송합니다.

`alert-state` 브랜치의 `alerts.db`에 정기/테스트 종류와 한국 날짜별 발송 상태를 보존합니다. SMTP 전에 `pending`을 원격 저장하고 접수 후 `sent`를 기록합니다. `sent`는 재발송하지 않으며, 접수 여부가 불명확한 `pending`·`uncertain`도 자동 재발송하지 않습니다. 오전 7시 20분 재확인은 이 기록을 공유합니다. 상태 브랜치를 삭제하거나 강제 초기화하지 않습니다.

공개 저장소의 상태 DB에는 종목·목표가·발송 기록이 포함됩니다. 자격 증명은 포함하지 않습니다.

## 수동 확인과 전체 목록 테스트 메일

[워크플로](https://github.com/roy888682/naver-stock-close-alert/actions/workflows/daily-close-alert.yml)에서 **Run workflow**, 브랜치 `main`을 선택합니다.

| action | 동작 |
| --- | --- |
| `check-only` | 사이트의 현재 목록을 읽고 종가 확인. 메일은 발송하지 않음 |
| `check` | 현재 목록으로 오늘의 정기 보고서 발송. 오늘 이미 보냈으면 생략 |
| `test-digest` | 현재 목록을 테스트 제목으로 발송. 정기 보고서와 별도의 날짜별 기록 |
| `test-email` | 기존 SMTP 연결 테스트 |
| `lookup` | 입력한 시장·티커의 종목명 조회 |

사이트 조회 단계가 성공한 다음에만 발송 상태를 복원하고 실행합니다. 부분 오류가 있어도 발송 결과 상태 저장 단계는 실행됩니다. 실패한 실행은 GitHub Actions 로그에서 확인하며, 실패 알림은 GitHub의 Actions 알림 설정을 따릅니다.

PC 대시보드와 Google Apps Script는 별도 프로그램으로 기존 조건·일정을 사용합니다. 이 GitHub 예약과 자동 공유되지 않습니다.
