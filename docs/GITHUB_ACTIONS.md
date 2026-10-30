# 웹 화면으로 등록하는 매일 오전 7시 종가 알림

한국·미국 종목의 티커·목표가·위쪽 +%·아래쪽 -%를 **GitHub 웹 입력 화면**에 저장합니다. 프로그램 설치나 별도 GitHub 토큰, JSON 파일 편집·업로드 없이 사용할 수 있습니다. 저장소에 쓰기 권한이 있는 GitHub 계정으로 로그인하세요.

저장한 조건은 한국 시간 매일 오전 7시에 확인합니다. 최신 완료 정규장 종가가 범위 안이면 `royhan88888@gmail.com`에서 `ykhan@dacpole.com`으로 메일을 보냅니다. PC를 켜 두지 않아도 됩니다. 처음 감시 목록은 비어 있으며 예시 종목이 자동 등록되지는 않습니다.

## 1. 웹에서 종목과 가격 범위 등록

**[종목 알림 등록·수정 (웹 입력) 열기](https://github.com/roy888682/naver-stock-close-alert/actions/workflows/manage-alerts.yml)**

1. 오른쪽 **Run workflow** 버튼을 누르고 브랜치 **`main`**을 선택합니다.
2. `operation`을 **`save`**로 선택합니다.
3. 아래 표에 따라 시장·티커·목표가·허용 비율을 입력합니다.
4. 초록색 **Run workflow**를 누릅니다. 새 실행 항목을 열고 완료될 때까지 기다립니다.
5. **초록색 체크가 표시되면 저장 완료**입니다. 실행의 **Summary**에서 자동 조회한 종목명과 저장된 조건을 확인합니다.

| 입력란 | 입력 방법 | 삼성전자 예시 |
| --- | --- | --- |
| `operation` | 등록·수정은 `save` | `save` |
| `market` | 한국 `KR`, 미국 `US` | `KR` |
| `ticker` | 한국 6자리 코드, 미국 영문 티커 | `005930` |
| `target_price` | 목표가. 한국은 원(KRW), 미국은 달러(USD) | `100000` |
| `upper_pct` | 목표가 위쪽 +%. 부호 없이 숫자만 입력 | `5` |
| `lower_pct` | 목표가 아래쪽 -%. 부호 없이 숫자만 입력 | `5` |
| `mode` | `daily`: 범위 안인 거래일마다 1회. `entry`: 최초 진입 및 이탈 후 재진입 때 1회 | `daily` |
| `rule_id` | 보통 비워 둡니다. 같은 시장·티커에 조건이 여러 개일 때 수정할 규칙의 ID | 빈칸 |

예시대로 입력하면 **95,000원 이상 105,000원 이하**를 감시합니다. 양 끝값을 포함하며 미국 종목은 환율을 적용하지 않습니다. 본인의 목표가격으로 바꿔 등록하세요. 최대 100개 규칙을 저장할 수 있습니다.

종목명은 저장 실행 중 네이버에서 자동 조회되어 Summary에 표시됩니다. 티커나 입력값이 잘못되면 저장하지 않고 실행이 실패합니다. 실패한 단계의 오류를 확인하고 값을 고쳐 다시 실행하세요. **Run workflow 버튼을 누른 것만으로 저장이 확정되지는 않습니다.**

### 현재 목록·수정·일시정지·재개·삭제

같은 입력 화면에서 `operation`을 바꿔 실행합니다.

| 작업 | `operation` | 필요한 입력 |
| --- | --- | --- |
| 현재 목록 확인 | `list` | 나머지 입력은 무시합니다. Summary에서 전체 목록과 `rule_id`를 확인합니다. |
| 신규 등록 또는 조건 수정 | `save` | 시장·티커·목표가·위아래 비율·알림 방식 |
| 일시정지 | `pause` | 시장·티커, 필요하면 `rule_id` |
| 다시 감시 | `resume` | 시장·티커, 필요하면 `rule_id` |
| 삭제 | `delete` | 시장·티커, 필요하면 `rule_id` |

`save`는 같은 시장·티커의 기존 규칙이 하나면 그 조건을 수정하고, 없으면 새로 등록합니다. 같은 시장·티커에 여러 규칙이 있으면 `list`로 확인한 `rule_id`를 입력해야 합니다. `pause`, `resume`, `delete`에서는 목표가·비율·알림 방식 입력을 무시하므로 기존 조건이 바뀌지 않습니다.

수정할 때 기존 값이 필요하면 먼저 `list`를 실행해 Summary를 확인한 뒤 새 목표가와 비율을 입력하세요. 티커·목표가·허용 비율·알림 방식이 바뀌면 새 조건으로 평가하므로 같은 거래일에도 새 알림을 받을 수 있습니다. 단순 일시정지·재개는 기존 발송 기록을 유지합니다.

저장 실행은 메일을 보내지 않습니다. 저장 완료 뒤 다음 오전 7시부터 새 조건을 사용하며, 바로 확인하려면 아래의 `check-only`를 실행합니다.

## 2. Gmail 앱 비밀번호 등록

이미 이 저장소에 **`SMTP_PASSWORD` Secret을 등록했다면 그대로 사용**합니다. 종목을 바꿀 때 다시 등록할 필요가 없습니다.

1. 발신 계정 **`royhan88888@gmail.com`**으로 Google에 로그인합니다.
2. 계정의 2단계 인증을 켜고 [앱 비밀번호](https://myaccount.google.com/apppasswords)를 발급합니다. [Google 안내](https://support.google.com/accounts/answer/185833?hl=ko)
3. GitHub 저장소에서 **Settings → Secrets and variables → Actions → New repository secret**을 엽니다.
4. **Name**은 `SMTP_PASSWORD`, **Secret**에는 발급한 16자리 앱 비밀번호를 공백 없이 넣고 **Add secret**을 누릅니다.

발신 `royhan88888@gmail.com`, 수신 `ykhan@dacpole.com`, 서버 `smtp.gmail.com`, 포트 `465`, SSL/TLS가 기본값입니다. 기존 `GMAIL_APP_PASSWORD` Secret도 대체 값으로 지원하며 둘 다 있으면 `SMTP_PASSWORD`를 우선합니다. 다른 저장소의 Secret은 자동으로 복사되지 않습니다.

앱 비밀번호는 Secret 입력란에만 넣습니다. 종목 등록 화면, 코드, 이슈에 넣지 않습니다. PC에 저장한 비밀번호도 Actions에 자동 전달되지 않습니다.

## 3. 테스트 후 자동 감시

**[오전 7시 알림 실행 화면](https://github.com/roy888682/naver-stock-close-alert/actions/workflows/daily-close-alert.yml)**의 **Run workflow**에서 브랜치를 `main`으로 선택합니다. 이 화면은 종목을 저장하는 화면과 별도입니다.

1. `action`을 **`test-email`**로 실행합니다. `ykhan@dacpole.com`의 수신함·스팸함에서 테스트메일을 확인합니다.
2. `action`을 **`check-only`**로 실행합니다. 저장한 종목의 종가·거래일·범위 판정을 로그에서 확인합니다. 메일을 보내거나 발송 판단 상태를 소모하지 않습니다.
3. 필요하면 `action`을 **`check`**로 실행합니다. 조건에 맞는 종목에 실제 알림을 보내고 중복 방지 상태를 저장합니다.

이후 매일 오전 7시 예약 실행이 자동으로 `check`를 수행합니다. 규칙이 없거나 모두 일시정지 중이거나 범위에 맞는 종목이 없으면 주가 알림 메일을 보내지 않습니다.

실패한 실행은 해당 실행의 실패한 단계를 엽니다. Gmail 인증 실패는 앱 비밀번호와 발신 계정을, 종가 조회 실패는 티커와 네이버 응답을 확인합니다. 발송 상태가 `pending` 또는 `uncertain`이면 수신함·발신 Gmail의 보낸 편지함을 먼저 확인하세요. 접수 여부가 불확실한 같은 거래일의 자동 재발송을 막습니다.

## 실행 시각과 종가 기준

매일 **한국 시간 오전 7시** 예약입니다. 한국은 전 영업일, 미국은 뉴욕 현지의 가장 최근 완료 정규장 종가를 사용하며 미국 서머타임도 고려합니다. 주말·휴장일은 같은 거래일로 중복 발송하지 않습니다.

예약식은 UTC 기준 `0 22 * * *`입니다. 실제 시작은 GitHub 혼잡에 따라 지연되거나 누락될 수 있습니다. 공개 저장소에서 60일간 활동이 없으면 예약 워크플로가 자동 중지될 수 있으므로 최근 실행 기록을 확인하세요. [GitHub 예약 실행 안내](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

장중 가격·시간외 가격은 확정 종가로 사용하지 않습니다. 같은 현지 날짜의 종가는 한국 16:35, 미국 뉴욕 17:00 이후이고 장 마감 정보가 일치할 때 사용합니다. 최신 완료 종가가 7일보다 오래되면 오류로 처리합니다. 네이버 공개 조회 경로는 공식 개발자 API가 아니므로 응답 형식이 바뀌면 보완이 필요할 수 있습니다.

## 기록과 공개 범위

**이 저장소는 공개 저장소입니다.** 저장한 종목·목표가·조건과 발송 기록은 다른 사람이 읽을 수 있습니다. 비밀번호는 Actions Secret에서만 전달하며 기록 DB에 저장하지 않습니다.

발송 기록은 `alert-state` 브랜치의 `alerts.db`에 보존합니다. 메일 발송 전에 발송 예정 상태를 먼저 저장하고 접수 결과도 저장하여 실행 중단·재실행의 중복 발송을 막습니다. 상태 브랜치나 DB를 삭제하면 이 기록이 사라집니다.

공개 저장소의 표준 GitHub 호스팅 실행기는 무료입니다. 비공개 저장소에는 계정의 포함 사용량과 과금 조건이 적용됩니다. [GitHub Actions 과금 안내](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

## 개발자용 내부 저장 방식

웹 입력 화면이 기본 브랜치의 `watchlist.json`을 검증하고 저장하며 오전 7시 실행이 이 파일을 읽습니다. 일반 사용자는 파일을 직접 편집할 필요가 없습니다. 개발자용 예시는 [watchlist.example.json](../watchlist.example.json)에 있습니다.

기존 규칙의 `id`는 중복 방지 기록과 연결되므로 유지합니다. 파일을 직접 수정할 때는 `stock_alert.cloud.validate_config`와 동일한 검증 기준을 지켜야 합니다. 직접 `main`에 조건 파일이나 감시 코드를 반영하면 메일 없는 자동 확인이 실행됩니다. 웹 저장 워크플로가 자체 토큰으로 만든 커밋은 별도의 push 워크플로를 다시 실행하지 않습니다.

로컬 단발 실행은 Python 의존성을 설치한 뒤 사용할 수 있습니다.

```text
python -m stock_alert.cloud --lookup KR 005930
python -m stock_alert.cloud --config watchlist.json --data-dir data --check-only
python -m stock_alert.cloud --test-email
python -m stock_alert.cloud --config watchlist.json --data-dir data
```

메일을 보내는 로컬 명령은 실행 환경의 `SMTP_PASSWORD`가 필요합니다. 같은 `--data-dir`를 유지해야 발송 기록이 이어집니다.

## 기존 PC·Google 버전

기존 [PC 대시보드](../README.md#windows에서-시작하기)는 프로그램이 켜져 있을 때, [Google Apps Script 버전](../apps_script/README.md)은 별도 설치·승인 후 기본 30분마다 확인합니다. 조건과 발송 기록은 GitHub 버전과 자동 동기화되지 않습니다. 같은 조건을 여러 방식으로 동시에 감시하면 각각 메일을 받을 수 있습니다.
