# 매일 오전 7시 GitHub 종가 알림

이 저장소의 GitHub Actions가 **한국 시간 매일 오전 7시**에 한국·미국 주식의 최신 완료 거래일 종가를 확인합니다. 목표가에서 정한 `-% ~ +%` 범위에 있으면 `royhan88888@gmail.com`에서 `ykhan@dacpole.com`으로 메일을 보냅니다. PC를 켜 두지 않아도 됩니다.

현재 `watchlist.json`은 비어 있습니다. 원하는 종목·목표가를 등록하고 Gmail 앱 비밀번호를 이 저장소의 Actions Secret에 저장하면 감시할 수 있습니다. 예시 파일의 가격은 설명용이며 예시 알림은 꺼져 있습니다.

`main`에 조건 파일이나 자동 실행 코드를 반영하면 메일 없는 `check-only` 검사가 자동 실행됩니다. 실제 발송은 오전 7시 예약 실행 또는 수동 `check`에서 수행합니다.

## 1. 종목과 가격 범위 등록

다음 두 방법 중 하나를 사용합니다.

### 화면에서 입력

1. 저장소를 내려받아 Windows에서 `run.bat`를 실행합니다. Python 3.11 이상이 필요합니다.
2. [로컬 화면](http://127.0.0.1:8765)에서 한국 또는 미국 시장을 선택하고 티커를 입력합니다. 종목명이 자동 조회됩니다.
3. 목표가와 위쪽 `+%`, 아래쪽 `-%`를 입력하고 등록합니다. 조건을 만족하는 거래일마다 받고 싶으면 **범위 안이면 거래일마다**를 선택합니다.
4. **GitHub 설정 JSON 내보내기** 버튼으로 `watchlist.json`을 내려받습니다.
5. GitHub 저장소의 기본 브랜치 `main`에 있는 [watchlist.json](../watchlist.json)을 내려받은 파일 내용으로 교체하고 커밋합니다. 이후 조건을 바꾸면 다시 내보내고 교체합니다.

로컬 화면의 저장 내용은 GitHub에 자동 동기화되지 않습니다. 이 화면은 PC에서 실행하며, GitHub Actions는 저장소의 JSON 파일을 읽어 정해진 시각에 실행합니다. 내보낸 파일에 Gmail 앱 비밀번호는 들어가지 않습니다.

### GitHub에서 직접 입력

[watchlist.example.json](../watchlist.example.json)을 참고해 [watchlist.json](../watchlist.json)을 편집합니다. 아래는 삼성전자 예시이며 목표가를 본인 조건으로 바꾸고 `enabled`를 `true`로 설정해야 감시합니다.

```json
{
  "version": 1,
  "rules": [
    {
      "id": "samsung-main",
      "market": "KR",
      "ticker": "005930",
      "target_price": "100000",
      "upper_pct": "5",
      "lower_pct": "5",
      "mode": "daily",
      "enabled": false
    }
  ]
}
```

| 항목 | 입력 방법 |
| --- | --- |
| `id` | 규칙마다 다른 고유 ID. 영문·숫자로 시작하는 1~64자이며 밑줄·하이픈을 사용할 수 있습니다. 같은 규칙을 수정할 때 그대로 유지합니다. |
| `market` | 한국 `KR`, 미국 `US` |
| `ticker` | 한국은 `"005930"`처럼 0을 포함한 6자리 문자열, 미국은 `"AAPL"` 같은 티커 |
| `name` | 선택 항목. 생략하면 조회된 종목명을 메일에 사용합니다. |
| `target_price` | 목표가. 한국은 원(KRW), 미국은 달러(USD). 소수점은 최대 6자리입니다. |
| `upper_pct` / `lower_pct` | 위쪽 / 아래쪽 허용 비율. 부호 없이 `"5"`처럼 입력합니다. |
| `mode` | `daily`: 범위 안인 거래일마다 1회. `entry`: 최초 진입 및 이탈 후 재진입 시 1회. 생략 시 `daily`. |
| `enabled` | `true`이면 감시, `false`이면 일시정지 |

목표가 100,000원, `lower_pct` 5, `upper_pct` 5이면 **95,000원 이상 105,000원 이하**를 감시합니다. 미국 종목은 환율을 적용하지 않습니다. 최대 100개 규칙을 등록할 수 있습니다.

같은 규칙의 순서를 옮기거나 이름만 고치면 기존 중복 방지 기록을 유지합니다. 티커·목표가·허용 비율·알림 방식을 바꾸면 새로운 조건으로 평가하므로 같은 거래일에도 새 알림을 받을 수 있습니다. `id`를 다른 규칙에 재사용하지 마세요.

## 2. Gmail 앱 비밀번호 등록

1. **발신 계정 `royhan88888@gmail.com`**으로 Google에 로그인합니다.
2. Google 계정에 2단계 인증을 설정한 뒤 [앱 비밀번호](https://myaccount.google.com/apppasswords)를 발급합니다. 일반 로그인 비밀번호와 다릅니다. 계정 정책에 따라 발급이 제한될 수 있습니다. [Google 안내](https://support.google.com/accounts/answer/185833?hl=ko)
3. GitHub 저장소에서 **Settings → Secrets and variables → Actions → New repository secret**을 엽니다.
4. 이름은 **`SMTP_PASSWORD`**, 값은 발급한 앱 비밀번호로 저장합니다.

기본 메일 설정은 발신 `royhan88888@gmail.com`, 수신 `ykhan@dacpole.com`, 서버 `smtp.gmail.com`, 포트 `465`, SSL/TLS입니다. 기존 `GMAIL_APP_PASSWORD` Secret도 대체 값으로 지원하며 둘 다 있으면 `SMTP_PASSWORD`를 우선합니다. 다른 저장소의 Secret은 자동으로 복사되지 않습니다.

앱 비밀번호는 Secret 입력란에만 넣습니다. `watchlist.json`, 코드, 이슈, Actions 실행 입력란에 넣지 않습니다. PC 화면에 저장한 비밀번호도 Actions에 자동 전달되지 않습니다.

## 3. 테스트 후 자동 감시

저장소의 **Actions → Daily close alert (07:00 KST) → Run workflow**에서 브랜치를 `main`으로 선택합니다. Actions가 비활성 상태라면 먼저 활성화합니다.

1. `action`을 **`test-email`**로 실행합니다. `ykhan@dacpole.com`의 수신함·스팸함에서 테스트메일을 확인합니다.
2. `action`을 **`check-only`**로 실행합니다. 등록한 종목의 종가·거래일·범위 판정을 실행 로그에서 확인합니다. 메일을 보내거나 발송 판단 상태를 소모하지 않습니다.
3. `action`을 **`check`**로 실행합니다. 조건이 맞는 종목에 실제 알림을 보내고 중복 방지 상태를 저장합니다.

이후 매일 오전 7시 예약 실행은 자동으로 `check`를 수행합니다. 규칙이 없거나 모두 일시정지되어 있으면 주가 알림을 보내지 않습니다. 조건에 맞는 종목이 없어도 메일을 보내지 않습니다.

티커만 확인하려면 `action`을 **`lookup`**, `market`을 `KR` 또는 `US`, `ticker`를 `005930` 또는 `AAPL`로 입력합니다. 종목명과 통화가 로그에 표시되며 감시 조건을 등록하지는 않습니다.

실패한 실행은 Actions에서 해당 실행과 실패한 단계를 엽니다. Gmail 인증 실패는 앱 비밀번호·발신 계정을, 종가 조회 실패는 티커와 네이버 응답을 확인합니다. 발송 상태가 `pending` 또는 `uncertain`이면 메일 접수 여부가 불확실하여 같은 거래일 자동 재발송을 막습니다. 수신함·보낸 편지함과 기록을 먼저 확인하세요.

## 실행 시각과 종가 기준

예약식은 UTC 기준 `0 22 * * *`입니다. **UTC 22시는 다음 날 한국 오전 7시**이며 한국 시간에는 서머타임이 없습니다. 그때 한국은 전 영업일, 미국은 뉴욕 현지의 가장 최근 완료 정규장 종가를 사용합니다. 미국 서머타임 전환도 시세 검증에 반영합니다. 주말·휴장일은 최신 완료 거래일을 유지하고 같은 거래일을 중복 발송하지 않습니다.

GitHub는 예약 시각의 정확한 실행을 보장하지 않습니다. 정각에는 지연이 생기거나 실행이 누락될 수 있습니다. 예약 워크플로는 기본 브랜치에 있어야 하고, 공개 저장소에서 60일간 활동이 없으면 자동 중지될 수 있습니다. 정기적으로 Actions의 최근 성공 기록을 확인하고 중지되었다면 다시 활성화하세요. [GitHub 예약 실행 안내](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

네이버의 일별 데이터에 섞인 장중 가격은 제외합니다. 같은 현지 날짜의 종가는 한국 16:35, 미국 뉴욕 17:00 이후이고 장 마감 정보가 일치할 때 사용합니다. 최신 완료 종가가 7일보다 오래되면 오류로 처리합니다. 네이버 공개 조회 경로는 공식 개발자 API가 아니므로 응답 형식이나 접근 정책이 변경되면 보완이 필요할 수 있습니다.

## 중복 방지 상태와 공개 범위

워크플로는 **`alert-state` 브랜치의 `alerts.db`**에서 이전 상태를 실행 폴더의 `data/alerts.db`로 복원하고 실행 후 저장합니다. 캐시 만료에 따라 발송 기록이 사라지는 것을 피하기 위한 영속 저장이며, 여러 실행이 겹치면 순서대로 처리합니다. 해당 브랜치와 DB를 삭제하면 중복 방지 기록이 사라져 기존 거래일에도 다시 메일을 보낼 수 있습니다.

메일을 보내기 전에 발송 예정 상태(`pending`)를 GitHub에 먼저 저장하고, 접수 결과도 저장합니다. 발송 중 실행이 중단되거나 결과 저장이 실패하면 이전 `pending` 또는 `uncertain` 기록을 기준으로 같은 거래일 자동 재발송을 막습니다. 이런 실패는 수신함·발신 Gmail의 보낸 편지함과 Actions 기록을 먼저 확인하세요. DB나 상태 브랜치를 지워 재시도하면 이미 접수된 메일이 중복 발송될 수 있습니다.

**이 저장소는 공개 저장소입니다.** `watchlist.json`의 종목·목표가·조건과 `alert-state`의 종가·이메일 주소·발송 기록은 다른 사람이 읽을 수 있습니다. 브랜치를 나눠도 비공개가 되지 않습니다. 비밀번호는 DB에 저장하지 않고 Secret에서 실행 중에만 전달합니다. 종목과 기록을 비공개로 보관하려면 저장소를 비공개로 바꾸고 Actions 무료 포함량과 예산을 확인하세요.

워크플로의 `contents: write` 권한은 상태 브랜치를 저장하는 데 필요합니다. 저장소 정책이나 브랜치 보호가 쓰기를 막으면 상태 저장 단계가 실패하므로 Actions 로그에서 원인을 확인합니다.

공개 저장소의 표준 GitHub 호스팅 실행기는 무료이며 이 워크플로는 `ubuntu-latest`를 사용합니다. 비공개 저장소에는 계정별 포함 컴퓨팅 사용량과 추가 과금 조건이 적용됩니다. [GitHub Actions 과금 안내](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

## 터미널에서 실행

Python 환경에서 `pip install -r requirements.txt`를 완료한 뒤 실행합니다.

```text
python -m stock_alert.cloud --lookup KR 005930
python -m stock_alert.cloud --lookup US AAPL
python -m stock_alert.cloud --config watchlist.json --data-dir data --check-only
python -m stock_alert.cloud --test-email
python -m stock_alert.cloud --config watchlist.json --data-dir data
```

메일을 보내는 명령은 실행 환경의 `SMTP_PASSWORD`가 필요합니다. 같은 `--data-dir`를 유지해야 중복 방지 상태가 이어집니다. PC용 대시보드와 별도 데이터 폴더를 사용하면 서로의 발송 기록을 공유하지 않습니다.

## 다른 실행 방식

기존 **[PC 대시보드](../README.md#windows에서-시작하기)**는 프로그램을 켜 둔 동안 기본 30분마다 확인합니다. **[Google Apps Script 버전](../apps_script/README.md)**도 별도 설치·승인 후 기본 30분 트리거로 동작합니다. 이 두 방식의 조건과 발송 상태는 GitHub 버전에 자동 동기화되지 않습니다. 같은 조건을 여러 방식으로 동시에 감시하면 각각 메일을 받을 수 있습니다.
