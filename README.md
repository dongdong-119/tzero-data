# tzero-data

로켓 발사 일정을 한 시간마다 수집해서 정적 JSON 파일로 공개하는 저장소예요.
서버와 DB 없이 GitHub Actions(수집)와 GitHub Pages(배포)만 써요.

```
GitHub Actions (매시 7분)
 └─ ingest.py: Launch Library 2 호출 → state/ 아카이브에 합치기 → data/ 공개 파일 생성
     └─ 바뀐 게 있으면 커밋 → GitHub Pages로 data/ 배포
```

## 공개 주소

기본 주소: `https://dongdong-119.github.io/tzero-data`

| 파일 | 내용 |
|---|---|
| `/upcoming.json` | 다가오는 발사 전체, 가까운 순 |
| `/months/YYYY-MM.json` | 그 달(UTC 기준)의 발사, 시간순. 지난 발사도 계속 보관 |
| `/index.json` | 존재하는 월 목록 `{"months": ["2026-06", ...]}` |

발사 1건의 모양:

```json
{
  "launch_id": "76617033-4dc0-4448-b50c-e5b0d5a18539",
  "name": "Falcon 9 Block 5 | USSF-317",
  "mission_name": "USSF-317",
  "provider": "SpaceX",
  "country": "USA",
  "rocket": "Falcon 9 Block 5",
  "rocket_family": "Falcon",
  "launch_time": "2026-11-18T03:14:00Z",
  "launch_time_precision": "minute",
  "launch_site": "Vandenberg SFB, CA, USA",
  "status": "Go",
  "mission_type": "Government/Top Secret",
  "orbit": "Low Earth Orbit",
  "image": "https://…/falcon_9.jpg",
  "source_url": "https://ll.thespacedevs.com/2.0.0/launch/…/"
}
```

### 클라이언트에서 알아둘 것

- **`launch_time`은 `Z`가 붙은 UTC ISO 8601이에요.** 예전 RocketParser API는 `Z` 없이
  내려줘서 클라이언트가 직접 붙였는데, 여기서는 붙이면 안 돼요.
- **`launch_time_precision`**: `minute`(시각 확정) / `day`(날짜만 확정) / `tbd`(미정,
  LL2가 월말·연말로 임의 표기). `upcoming.json`에는 `tbd`도 들어 있고, 월 파일에는
  `minute`·`day`만 들어 있어요.
- **`upcoming.json`은 최대 1시간쯤 묵은 파일일 수 있어요.** 클라이언트에서도
  `launch_time >= 지금`으로 한 번 더 걸러 주세요.
- **월 파일은 UTC 기준이에요.** 한국 시간으로 캘린더를 그리려면 보는 달의 앞뒤 달
  파일까지 받아서 현지 날짜로 다시 묶으세요. 없는 달은 `index.json`으로 확인할 수 있어요.
- 수집 대상 발사사는 SpaceX, Rocket Lab, United Launch Alliance, Arianespace예요
  (`app/config.py`의 `PROVIDER_LSP_IDS`).

## 구조

```
ingest.py            수집 실행 스크립트 (CLI)
app/
  config.py          상수, 수집 대상 발사사, 파일 경로
  ll2.py             Launch Library 2 HTTP 클라이언트 (표준 라이브러리만 사용)
  parser.py          LL2 응답 → 레코드 변환 (순수 함수)
  store.py           state/ JSON 아카이브 읽기·쓰기
  publish.py         아카이브 → data/ 공개 파일 생성
  report.py          이번 실행의 변경 사항 → Slack 알림 요약
state/               내부 원본. 직접 고치지 않아요
  launches.json      전체 발사 아카이브 (지난 발사 포함, 삭제하지 않음)
  sweep.json         피드에서 사라진 발사를 재조회하기 위한 장부
data/                공개 파일. GitHub Pages로 그대로 배포돼요
tests/               네트워크 없이 도는 테스트
.github/workflows/ingest.yml   매시간 수집 + 배포
```

## 로컬에서 실행

Python 3.9 이상만 있으면 돼요. 설치할 패키지는 없어요.

```bash
python3 -m unittest discover -s tests
```

```bash
python3 ingest.py
```

LL2 무료 API는 IP당 한 시간에 15번까지만 부를 수 있어요. 수집 한 번에 5~8번을 써요.

## 수동 실행과 백필

- **수동 실행**: GitHub 저장소의 Actions → "Ingest & publish" → Run workflow.
- **과거 발사 채우기**: 같은 화면의 `backfill_from`에 날짜(`2026-06-01` 형식)를 넣고 실행하면,
  그 날짜 이후에 끝난 발사를 발사사별로 채워요. 발사사당 1~4번을 더 불러요.

## Slack 알림

저장소 시크릿 `SLACK_WEBHOOK_URL`에 Slack 수신 웹훅(Incoming Webhook) 주소를 넣으면 알림이 와요.
시크릿이 없으면 알림만 건너뛰고 수집은 그대로 돌아요.

```bash
gh secret set SLACK_WEBHOOK_URL --repo dongdong-119/tzero-data
```

매시간 보내면 소음이라 아래 경우에만 보내요.

- **알릴 만한 변경이 있을 때**: 새 발사, 상태 변경(예: Go → Success), 확정된 일정의 발사 시각 변경.
  시각은 한국 시간으로 적어요. 미정(`tbd`) 일정의 임시 날짜 이동이나 이미지 변경 같은 건 알리지 않아요.
- **실패했을 때**: 수집·배포 실패, 또는 LL2 호출이 전부 실패한 경우.
- **수동 실행했을 때**: 변경이 없어도 결과를 보내요. 연결 테스트로 쓸 수 있어요.

알림 기준은 `app/report.py`, 보내는 조건은 워크플로우의 `notify` 작업에 있어요.

## 실패했을 때

- LL2 호출이 일부 실패하면 경고만 남기고, 받은 만큼만 반영해요. 기존 데이터는 지워지지 않아요.
- LL2 호출이 전부 실패하면 `ll2-health` 작업이 빨간불로 표시돼요. 다음 시간에 다시 시도해요.

## 데이터 출처

발사 데이터는 [The Space Devs](https://thespacedevs.com/)의 Launch Library 2 API에서 가져와요.
