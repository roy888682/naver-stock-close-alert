# PC를 꺼도 무료로 종가를 확인하는 방법

확인 기준일: **2026-10-30**. 공식 서비스 문서를 기준으로 비교했습니다. **Google Apps Script 변환 버전을 구현했습니다.** [설치 안내](../apps_script/README.md)에 따라 본인 Google 계정에서 권한을 승인하고 `setup`을 실행하면 30분 자동 감시를 등록합니다. 실제 계정에서 설정을 완료해야 동작합니다.

종가는 거래일마다 한 번 갱신되므로 **30분 또는 1시간 간격의 자동 확인**을 권합니다. 여기서 “24시간 감시”는 PC를 꺼도 정기적으로 확인한다는 뜻입니다. 무료 서비스에서 정확한 시각의 실행이나 무중단 동작을 보장하는 것은 아닙니다.

## 추천 비교

| 방법 | PC를 꺼도 실행 | 필요한 추가 작업 | 판단 |
| --- | --- | --- | --- |
| **Google Apps Script** | 시간 트리거로 가능 | 구현된 `apps_script` 버전을 Google 계정에 설치·승인 | **개인용 무료 알림에 우선 추천** |
| **GitHub Actions** | 예약 워크플로로 가능 | 단발 실행 워크플로, 조건 전달, 영속적인 발송 상태 저장 | Python 재사용은 쉽지만 이용 조건·할당량 확인 필요 |
| **Render 무료 웹 서비스** | 대기 상태에서 중지됨 | 저장소와 메일 발송 방식을 모두 변경 | 현재 SQLite·SMTP 구성에는 부적합 |

## 1. Google Apps Script

설치형 시간 트리거는 브라우저나 PC를 켜 두지 않아도 지정한 간격으로 스크립트를 실행합니다. 트리거를 만든 Google 계정의 권한으로 동작하며 실행 시각에는 변동이 있을 수 있습니다. [Google 시간 트리거 안내](https://developers.google.com/apps-script/guides/triggers/installable#time-driven_triggers)

`MailApp`으로 본인 계정에서 메일을 보내면 Gmail 앱 비밀번호를 따로 저장할 필요가 없습니다. 처음 사용할 때 본인이 메일 발송 권한을 승인합니다. `MailApp`은 메일 발송 서비스이며 Gmail 수신함을 읽는 기능은 없습니다. [Google MailApp 안내](https://developers.google.com/apps-script/reference/mail/mail-app)

일반 Gmail 계정의 주요 한도는 이메일 **하루 수신자 100명**, URL 조회 **하루 20,000회**, 트리거 실행 시간 **하루 총 90분**, 실행 한 번당 **6분**입니다. 한 명에게 메일을 10번 보내면 수신자 할당량을 10만큼 사용합니다. 한도를 넘으면 실행이 중단되며 한도는 변경될 수 있습니다. [Google Apps Script 할당량](https://developers.google.com/apps-script/guides/services/quotas)

적용할 구성은 다음과 같습니다.

1. Google Sheets에 시장·티커·목표가·상단%·하단%·알림 방식을 입력합니다.
2. Apps Script가 네이버의 완료된 정규장 종가를 조회하고 목표 범위 진입 여부를 판단합니다. 미국 거래일과 서머타임도 처리합니다.
3. 이전 거래일과 발송 상태를 저장합니다. 작은 상태값은 `PropertiesService`에 유지할 수 있습니다. [Google Properties Service 안내](https://developers.google.com/apps-script/guides/properties)
4. 새로운 알림만 `MailApp`으로 보냅니다. 여러 종목을 한 메일로 묶으면 발송 할당량을 아낄 수 있습니다.
5. 30분 또는 1시간 트리거를 등록하고 실행 기록과 오류 알림을 확인합니다.

JavaScript로 옮긴 코드는 [apps_script](../apps_script/README.md)에 있습니다. `setup`은 Google에서 네이버 종가를 실제로 조회한 뒤 자동 실행을 등록합니다. **테스트메일과 실제 트리거 실행 기록도 확인**하세요. Google 계정 권한이 취소되거나 할당량에 도달하면 정상 실행할 수 없습니다.

## 2. GitHub Actions

일반 GitHub 호스팅 실행기는 공개 저장소에서 무료입니다. 비공개 저장소는 GitHub Free의 **월 $12 포함 컴퓨팅 사용량**에서 차감됩니다. 표준 Linux 요금 `$0.006/분` 기준 약 2,000분에 해당하지만, 다른 비공개 Actions 작업과 관련 클라우드 컴퓨팅도 같은 포함량을 사용합니다. 이 저장소만의 별도 무료 할당량이 아닙니다. 결제수단을 등록했다면 포함량을 넘을 때 추가 과금될 수 있어 사용 예산을 제한해야 합니다. 결제수단이 없으면 포함량 소진 시 실행이 중단됩니다. [GitHub Actions 과금 안내](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

각 작업의 사용 시간은 분 단위로 올림됩니다. 31일 동안 매시간 한 번, 작업당 2분이면 약 **1,488분**입니다. 15분마다 실행하면 최소 1분짜리 작업이어도 **2,976분**이므로 표준 Linux 기준 무료 포함량을 넘을 수 있습니다. 실제 실행 시간과 다른 작업 사용량을 확인해야 합니다. [GitHub 실행기 요금과 올림 기준](https://docs.github.com/en/billing/reference/actions-runner-pricing)

예약 실행은 기본 브랜치에서 동작하고 기본 시간대는 UTC입니다. 현재는 `America/New_York` 같은 IANA 시간대도 설정할 수 있습니다. 실행 지연·누락이 생길 수 있어 매시 정각을 피하는 편이 좋습니다. **공개 저장소는 60일 동안 저장소 활동이 없으면 예약 워크플로가 자동 중지**됩니다. [GitHub 예약 실행 조건](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

GitHub Actions의 추가 약관은 개발·테스트 용도와 서버리스 애플리케이션 사용 등에 제한을 둡니다. 이 제한을 고려하면 일반 운영 서버를 대신하는 장기 무료 호스팅으로 무조건 권하기는 어렵다고 판단합니다. **개인 알림 운영에는 Apps Script를 우선 권합니다.** [GitHub Actions 이용 조건](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)

Python 코드를 활용하는 예약 실행을 추가한다면 다음 작업이 필요합니다.

- 로컬 화면의 종목·목표가 조건을 Actions Secret이나 별도 비공개 저장소를 통해 전달하고 한 번의 확인을 실행합니다.
- SMTP 비밀번호는 Actions Secret으로 전달합니다. 비밀번호와 개인 관심 종목 목록을 공개 저장소에 넣지 않습니다.
- 이전 발송 상태를 영속적으로 저장하고 같은 거래일의 중복 발송을 막습니다. 캐시는 **7일 넘게 접근하지 않으면 삭제**될 수 있으므로 유일한 발송 기록 저장소로 의존하면 안 됩니다. [GitHub 캐시 보관 정책](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#usage-limits-and-eviction-policy)
- 발송 상태를 별도의 비공개 저장소나 외부 영속 저장소에 보관하고 필요한 권한만 줍니다. 공개 저장소의 일부 브랜치만 비공개로 만들 수는 없습니다. 조회만 하는 작업은 `contents: read`로 제한합니다. [GitHub 토큰 권한](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions)

Gmail 연결은 먼저 `smtp.gmail.com:465`에 TLS 연결하고 `EHLO` 후 종료하는 검사로 확인할 수 있습니다. 비밀번호나 메일 전송 없이 연결만 검증하는 방법이며, 인증과 실제 수신 검사는 별도로 필요합니다. 이번 조사에서는 Actions의 SMTP 연결이나 실제 메일 발송을 실행하지 않았습니다.

## 3. Render 무료 요금제

무료 웹 서비스는 **15분 동안 요청이 없으면 중지**되고 재시작·재배포·중지 시 로컬 파일 변경 사항이 사라집니다. 무료 서비스에는 영속 디스크를 붙일 수 없습니다. 또한 SMTP 포트 **25·465·587로의 외부 연결이 차단**되므로 현재 SQLite와 Gmail SMTP 구성을 그대로 사용할 수 없습니다. [Render 무료 서비스 제한](https://render.com/docs/free)

## 다른 무료 서비스의 제약

- **PythonAnywhere**: 새 무료 계정은 2026-01-15 이후 예약 작업을 제공하지 않습니다(EU는 2026-01-08 기준). 상시 실행 작업도 유료 계정용이고 무료 계정의 외부 인터넷 접근은 허용 목록으로 제한됩니다. [무료 계정 기능](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/), [상시 작업](https://help.pythonanywhere.com/pages/AlwaysOnTasks/), [외부 접근 제한](https://help.pythonanywhere.com/pages/403ForbiddenError/)
- **Oracle Always Free VM**: 무료 범위 내 서버 운영은 가능하지만 가입 시 신원 확인용 카드가 필요하고 지역 용량이 부족하면 서버를 만들지 못할 수 있습니다. 사용량이 낮은 인스턴스는 회수될 수도 있어 작은 개인 알림을 처음 운영하는 방법으로는 관리 부담이 있습니다. [가입 조건](https://www.oracle.com/cloud/free/faq/), [무료 컴퓨팅 제한](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

무료 서비스에서도 실행 기록과 실패를 확인하는 운영은 필요합니다. 네이버 데이터 조회 경로나 각 서비스의 무료 정책이 바뀌면 프로그램이나 설정을 수정해야 할 수 있습니다.
