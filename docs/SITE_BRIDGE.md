# 웹 목록과 오전 7시 이메일 연결

사용자는 **[Close Watch 웹사이트](https://close-watch-list.hansharedid.chatgpt.site)**에서 티커·목표가·위아래 비율을 등록합니다. 등록 순서대로 번호가 표시되고, 항목 오른쪽 `×`로 삭제합니다. 조건은 사이트 데이터베이스에 저장됩니다.

매일 한국 시간 오전 7시에 사이트에 연결된 클라우드 작업이 최신 조건을 이 저장소에 전달합니다. GitHub Actions는 전달된 날짜와 사이트를 검증한 후 확정 종가를 확인하고, 기존 `SMTP_PASSWORD` Secret으로 `royhan88888@gmail.com`에서 `ykhan@dacpole.com`으로 메일을 보냅니다. 사용자가 GitHub 입력창이나 JSON 파일을 다룰 필요는 없습니다.

## 실행 구조

- 사이트: `appgprj_6ae4941c8a3c8191b9c7fe8ed95ee3fa` (소유자 전용)
- 현재 조건: 사이트 D1의 `alert_rules` 테이블
- 전달 스냅샷: 기본 브랜치의 `watchlist.json`
- 실행 요청: `cloud-run-request.json`
- 알림 기록: `alert-state` 브랜치의 `alerts.db`

기존 GitHub cron은 사이트 연결 작업으로 대체했습니다. 따라서 오전 7시 실행 전에 최신 웹 조건이 전달되어야 하며, 이전 저장소 조건만으로 예약 발송하지 않습니다. 일반 코드·조건 파일 변경은 메일 없는 확인만 실행합니다. 수동 `check-only`, `check`, `test-email`, `lookup`은 기존 Actions 화면에서 유지됩니다.

GitHub에는 조건과 발송 기록이 전달되므로 공개 저장소에서는 해당 정보도 공개됩니다. 사이트 자체의 소유자 전용 접근 범위와는 별개입니다. 메일 비밀번호는 GitHub Secret에만 유지됩니다.

## 연결 클라우드 작업의 저장된 실행 지침

다음 지침은 사이트에 연결된 오전 7시 작업이 작성 세션 없이 실행할 수 있도록 기록한 것입니다. 접근 토큰이나 비밀번호를 이 파일 또는 작업 프롬프트에 넣지 않습니다.

1. native Sites `get_site`로 위 사이트 ID와 소유권을 확인합니다. native `read_database_overview`로 실제 D1 바인딩과 `alert_rules` 테이블을 찾고, 반환된 정확한 식별자로 `read_database_table_rows`를 호출합니다. 결과가 누락·오류·잘림이면 중단합니다. 필요하면 도구가 반환한 `next_offset`만 사용해 페이지를 이어 읽습니다. 100개를 초과하면 중단합니다. 사이트 서비스 토큰이나 BYOP 연결을 새로 만들지 않습니다.
2. 각 행에서 `id`, `market`, `ticker`, `name`, `target_price`, `upper_pct`, `lower_pct`, `mode`, `enabled`만 선택합니다. `revision`, 생성·수정 시각은 전달하지 않습니다. `id`는 사이트에 저장된 값을 그대로 유지합니다. `enabled`가 정수 `0` 또는 `1`이면 JSON 불리언으로 바꾸고, 이미 불리언이면 그대로 둡니다. 다른 형식은 거절합니다. 정렬은 `created_at`, 같은 시각이면 `id` 기준으로 고정합니다. 규칙이 실제로 0개인 정상 조회는 빈 목록으로 전달하여 삭제를 반영합니다. 조회 실패를 빈 목록으로 바꾸지 않습니다.
3. 전체 문서는 `{ "version": 1, "rules": [...] }`입니다. `stock_alert.cloud.validate_config`의 제한을 적용합니다: ID 1~64자의 영문·숫자·`_`·`-`, 중복 없음, 시장 `KR`/`US`, 한국 티커 6자리 문자열(앞의 0 유지), 미국 티커 최대 20자, 목표가 유한한 양수 최대 1조, 상단 0~1000%, 하단 0~100%, 수치 소수점 6자리 이하, 방식 `daily`/`entry`, 이름 최대 80자·제어문자 없음, enabled 불리언, 최대 100개입니다. 숫자는 문자열로 전달합니다. 잘못된 행 하나라도 있으면 전체 전달을 중단합니다.
4. native GitHub로 `roy888682/naver-stock-close-alert`의 현재 기본 브랜치 head와 그 commit의 tree를 읽습니다. 같은 head에서 기존 `cloud-run-request.json`도 읽습니다(최초에는 없음). 한국 날짜는 `Asia/Seoul` 기준의 오늘 `YYYY-MM-DD`입니다. 기존 요청이 같은 사이트·오늘 날짜라면 이미 요청한 실행이므로 새 요청을 만들지 않습니다. 다른 사이트 ID·잘못된 버전·미래 날짜의 기존 요청은 정상 재시도로 간주하지 말고 중단하여 확인합니다. 이전 실행 상태를 확인하고 완료·실패 또는 필요한 사용자 조치가 있을 때만 알립니다.
5. 한 tree에 검증된 `watchlist.json`과 다음 요청을 함께 반영합니다.

   ```json
   { "version": 1, "site_id": "appgprj_6ae4941c8a3c8191b9c7fe8ed95ee3fa", "request_date": "YYYY-MM-DD" }
   ```

   `request_date`는 예시 문자가 아니라 오늘 한국 날짜로 치환합니다. 두 파일은 UTF-8 JSON으로 작성하고 다른 경로는 변경하지 않습니다. native `create_tree`에 현재 tree를 기반으로 두 파일을 넣고, `create_commit`에 현재 head를 단일 부모로 사용한 후, `update_ref`의 `expected_sha`를 같은 head로 설정해 `force:false`로 기본 브랜치를 이동합니다. 커밋 메시지는 `Request daily closing-price alerts from the private Site`를 사용합니다. 파일별 별도 커밋이나 force push는 사용하지 않습니다.
6. ref 충돌 또는 완료 여부가 불명확한 응답은 원격 head와 요청 파일을 다시 읽어 판별합니다. 이미 오늘 요청이 저장되었으면 재생성하지 않습니다. 외부 변경을 덮지 않습니다. 전달 성공 후 해당 commit의 `Daily close alert (07:00 KST)` 실행을 확인합니다. 완료·실패 또는 사용자 조치가 필요한 경우에만 알려주며, 변동 없는 정상 상태에 반복 알림을 보내지 않습니다. 클라우드 작업은 직접 Gmail을 보내지 않습니다. 실제 주가 알림은 기존 SMTP 계정으로 Actions가 발송합니다.

## 중복 방지와 오류

`scripts/site_bridge.py`는 실행 요청 파일이 변경된 commit에서만 실제 확인·발송을 허용합니다. 사이트 ID, 버전, 오늘 한국 날짜가 맞지 않으면 발송하지 않습니다. push 작업은 요청과 조건이 같은 스냅샷인 `github.sha`를 확인합니다.

메일 발송 전후의 영속 상태 저장과 같은 거래일 중복 방지는 기존 로직을 사용합니다. 접수 여부가 불명확한 `pending`·`uncertain` 기록은 자동 재발송하지 않습니다. 실패하거나 취소된 workflow는 실행 단계와 보낸 편지함을 확인합니다. 취소 상태만으로 메일이 발송되지 않았다고 판단하거나 오늘 요청을 새로 만들지 않습니다. 다음 날에는 다음 한국 날짜의 요청을 전달할 수 있지만 같은 종가·거래일에 남은 불명확한 기록은 계속 차단됩니다. 웹 목록을 수정·삭제하면 다음 사이트 연결 작업에서 반영되며, 이미 보낸 메일은 취소하지 않습니다.

사이트에 연결된 일정의 존재·활성 상태와 실제 D1 조회 권한을 확인한 후 이 bridge를 운영합니다. 연결 작업이 실행되지 않으면 GitHub가 이전 조건으로 대신 발송하지 않으므로 사이트 연결 작업과 Actions의 최근 완료 기록을 함께 확인하세요.
