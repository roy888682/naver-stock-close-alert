# PC를 꺼도 종가 알림을 받는 방법

확인 기준일: **2026-10-30**. 이 저장소는 GitHub Actions와 Google Apps Script 실행 방식을 제공합니다. **매일 오전 7시 확인하려면 [GitHub Actions 설치 안내](GITHUB_ACTIONS.md)**를 따르세요.

| 방법 | 자동 확인 시각 | 조건 입력 | 발송 인증 |
| --- | --- | --- | --- |
| **GitHub Actions** | 한국 시간 매일 오전 7시 예약 | [GitHub 웹 입력 화면](https://github.com/roy888682/naver-stock-close-alert/actions/workflows/manage-alerts.yml)에서 저장 | 이 저장소의 `SMTP_PASSWORD` Secret |
| **Google Apps Script** | 기본 30분마다 | Google Sheets에서 종목·가격 조건 편집 | 발신 Google 계정에서 최초 권한 승인 |
| **PC·Docker 버전** | 기본 30분마다 | PC 대시보드 | 대시보드에 앱 비밀번호 저장 |

GitHub와 Google 버전은 PC를 꺼도 실행합니다. PC·Docker 버전은 실행 중인 컴퓨터나 서버가 필요합니다. 조건과 발송 기록은 버전 사이에 자동으로 공유되지 않으므로 같은 조건을 여러 방식으로 실행하면 각각 메일을 받을 수 있습니다.

## GitHub에서 매일 오전 7시

이미 구현된 [.github/workflows/daily-close-alert.yml](../.github/workflows/daily-close-alert.yml)이 정해진 시각에 Python을 실행합니다. 원하는 종목과 목표가·위아래 비율을 [웹 입력 화면](https://github.com/roy888682/naver-stock-close-alert/actions/workflows/manage-alerts.yml)에 저장하고 발신 Gmail의 앱 비밀번호를 Secret으로 등록하면 됩니다. 프로그램 설치나 별도 GitHub 토큰, JSON 파일 편집·업로드가 필요 없습니다. 현재 감시 목록은 비어 있으므로 본인 조건을 먼저 등록합니다.

실행할 때마다 이전 거래일과 발송 기록을 `alert-state` 브랜치에서 복원하고 저장하여 휴장일이나 같은 거래일 재실행의 중복 발송을 막습니다. **공개 저장소의 종목·목표가와 상태 브랜치 기록은 공개**됩니다. 비밀번호는 Actions Secret에서 전달하며 상태 DB에 저장하지 않습니다.

공개 저장소의 표준 GitHub 호스팅 실행기는 무료입니다. 비공개 저장소를 사용하면 계정의 포함 컴퓨팅 사용량과 추가 과금 조건이 적용됩니다. [GitHub Actions 과금 안내](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

오전 7시는 예약 시각이며 실제 시작은 GitHub 혼잡에 따라 지연되거나 누락될 수 있습니다. 공개 저장소에서 60일간 활동이 없으면 예약 워크플로가 자동 중지될 수 있으므로 Actions의 최근 성공 기록을 확인합니다. [GitHub 예약 실행 안내](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

**[설정과 테스트 순서 보기](GITHUB_ACTIONS.md)**

## Google Sheets에서 관리하려면

[Google Apps Script 버전](../apps_script/README.md)을 발신 Google 계정에 설치하고 권한 승인 후 `setup`을 실행합니다. 티커를 입력하면 종목명이 조회되며 Google 서버에서 기본 30분마다 확정 종가를 확인합니다. Gmail 앱 비밀번호를 별도로 저장하지 않습니다.

Google 버전은 매일 오전 7시 GitHub 워크플로와 별개입니다. Google 서비스의 메일·실행 시간 할당량이 적용되며 정확한 실행 시각을 보장하지는 않습니다. 현재 한도는 [Google Apps Script 할당량 안내](https://developers.google.com/apps-script/guides/services/quotas)에서 확인합니다.

## PC에서 사용할 때

[Windows·Docker 설치 안내](../README.md#windows에서-시작하기)에 따라 실행합니다. 대시보드에서 종목과 메일 설정을 저장하며, 기본 자동 확인은 30분마다 동작합니다. 프로그램이 종료되거나 컴퓨터가 절전·종료되면 해당 PC의 확인도 중단됩니다.

네이버의 공개 시세 조회 경로가 변경되면 어느 실행 방식에서든 조회 오류가 발생할 수 있습니다. 오류가 난 실행의 로그를 확인하고 티커나 연결 문제를 해결한 뒤 다시 확인합니다.
