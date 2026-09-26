# 데스크톱 앱 (Phase 12)

Argos를 macOS 앱으로 설치해서 쓴다. 앱 안에 Argos 서버(PyInstaller로 묶은 백엔드)와 빌드된 화면이 들어
있고, 창은 서버가 `http://127.0.0.1:8000`에서 서빙하는 화면을 연다. 도커는 쓰지 않는다(이 Mac의
Keychain, Ollama, 에이전트 로그인, 옵시디언 볼트를 그대로 써야 해서).

## 설치

1. `make app` → `desktop/build/Argos.dmg`
2. dmg를 열어 Argos를 응용 프로그램 폴더로 끌어 놓는다.
3. 처음 한 번은 Finder에서 Argos를 **우클릭 → 열기**. Apple 개발자 서명(유료)이 아닌 ad-hoc 서명이라
   더블클릭으로는 "확인되지 않은 개발자" 경고가 뜬다.
4. 처음 열면 **환영 화면**이 뜬다: 설치된 에이전트 도구(Claude Code, Codex, Hermes, Ollama) 점검 →
   "Argos에 연결"(MCP 등록 + `argos` 스킬 설치) → 기본 에이전트·분류 모델·옵시디언 볼트 → 로그인 시
   자동 실행. 모든 단계는 건너뛸 수 있고 설정에서 다시 할 수 있다(설정 → 에이전트 도구 → "처음 설정
   다시 보기").

빌드에 필요한 것: Rust(rustup), Xcode 명령행 도구, uv, bun. 첫 빌드는 Rust 의존성 때문에 몇 분 걸린다.

## 동작

- **창**: 제목 표시줄 없이 창 버튼이 레일 위에 놓이고, 창 맨 위(20px)를 끌어 옮긴다(더블클릭 확대).
- **첫 실행**: 영역(학업·프로젝트)만 있고 과목·프로젝트는 비어 있다. 사이드바 + 버튼이나 에이전트에게
  "자료구조 과목 만들어 줘"(MCP `create_channel`)로 만든다.
- **메뉴 막대 상주**: 창을 닫으면 숨기만 하고 서버는 계속 돈다(캘린더·볼트 동기화, 잡, 알림). 창이 없으면 Dock 아이콘도 숨는다. 메뉴 막대 아이콘 → "Argos 열기", "Argos 종료".
- **빠른 입력**: 어디서든 `⌘⇧Space` → 한 줄 적고 Enter → 인박스로 들어가 분류된다. Esc로 닫기.
- **알림**: Argos 알림(마감, 오래 둔 인박스)을 macOS 알림으로 보여 준다. 창이 앞에 있으면 생략.
- **데이터**: `~/Library/Application Support/Argos` (DB, 백업, 볼트 백업, 기록 `logs/server.log`).
  저장소의 `backend/data`(개발용)와 따로다. 필요하면 이 폴더에 `.env`를 두어 `ARGOS_` 설정을 줄 수 있다.
- **비밀 값**: `.env` 없이 동작한다. Hermes API 키는 `~/.hermes/.env`의 `API_SERVER_KEY`를 읽기
  전용으로 찾고, iCloud 앱 암호는 Keychain, Claude·Codex는 각자의 로그인을 쓴다.
- **도구 경로**: Finder에서 연 앱은 PATH가 비어 있어서, 시작할 때 로그인 셸(`$SHELL -ilc`)의 PATH를
  한 번 읽어 `claude`, `codex`, `hermes`, `ollama`, `tailscale`을 찾는다.
- 앱이 강제 종료되면 서버도 2초 안에 스스로 끝난다(포트를 잡고 남지 않게).

## 삭제

설정 → 작업과 운영 → **Argos 완전 삭제**: 에이전트 도구의 Argos MCP·스킬, 로그인 시 자동 실행,
키체인의 iCloud 앱 암호를 지우고, 앱과 캐시(`~/Library/Caches`, `WebKit`, `Preferences` 등의
`app.argos.desktop`)를 휴지통으로 옮긴 뒤 종료한다. "내 데이터도"를 켜면 데이터 폴더도 휴지통으로 간다.
휴지통을 비우기 전까지는 되돌릴 수 있다.

## 에이전트 연결 (MCP와 스킬)

"Argos에 연결"은 각 도구의 방식으로 두 가지를 설치한다. 누르기 전에는 도구 설정을 건드리지 않는다.

| 도구 | MCP 등록 | 스킬 |
|---|---|---|
| Claude Code | `claude mcp add --transport http --scope user argos <주소>` | `~/.claude/skills/argos` |
| Codex | `codex mcp add argos --url <주소>` | `~/.codex/skills/argos` |
| Hermes | `hermes config set mcp_servers.argos.url <주소>` 후 게이트웨이 재시작 | `skills.external_dirs`에 Argos 스킬 폴더 추가 |

스킬(`integrations/skills/argos`, Hermes용은 `integrations/hermes/skills/argos`)은 언제 어떤 Argos 도구를
쓰는지 알려 준다. 앱이 업데이트되면 다음 시작 때 Argos가 설치한 스킬 복사본을 새 버전으로 바꾸고, 도구에
등록된 Argos MCP 주소가 달라졌으면 다시 등록한다(사용자가 지운 것은 다시 넣지 않는다). Argos가 설치한 스킬 폴더에는 `.installed-by-argos` 표시가 있고, 같은 이름의 다른
스킬이 있으면 덮어쓰지 않는다. 설정 → 에이전트 → 에이전트 도구에서 도구별로 **MCP 제거 / 스킬 제거 /
모두 제거**를 할 수 있다(Argos가 설치한 것만 지운다).

## 자동 업데이트

- 앱은 시작 20초 뒤와 6시간마다 `https://github.com/sciencemj/argos/releases/latest/download/latest.json`을
  확인한다. 새 버전이면 받아서 설치해 두고 알림을 띄운다. 메뉴 막대 "다시 시작해 업데이트" 또는 설정 →
  작업과 운영 → 앱 업데이트에서 다시 시작하면 적용된다(그냥 종료했다가 다시 열어도 된다).
- 업데이트 파일은 업데이트 전용 키로 서명되고, 앱은 공개 키(`tauri.conf.json`)로 확인한 것만 설치한다.
  개인 키는 `~/.tauri/argos-updater.key`(비밀번호는 키체인 `argos-updater-key-password`), CI에는 GitHub
  secret `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. **개인 키를 잃으면 설치된 앱에
  업데이트를 보낼 수 없으니 따로 백업해 둔다.**
- 새 DB 마이그레이션이 있는 버전으로 처음 켤 때는 먼저 DB를 백업한다.

### 릴리스하기

```bash
make release VERSION=0.2.0   # 버전 올림 → 커밋 → 태그 v0.2.0 → 푸시
```

태그가 올라가면 `.github/workflows/release.yml`이 macOS에서 빌드·서명하고 GitHub Release에 dmg,
업데이트 파일(`Argos_aarch64.app.tar.gz` + `.sig`), `latest.json`을 올린다. 올리기 전에 로컬에서 시험하려면
`ARGOS_UPDATE_URL=http://127.0.0.1:8765/latest.json`으로 다른 주소를 줄 수 있다(http는
`dangerousInsecureTransportProtocol`을 켠 빌드에서만).

## 포트

| | 서버 | 화면 |
|---|---|---|
| 데스크톱 앱 | 8000 (API와 화면을 한 포트에서) | 같음 |
| 개발 (`make dev`) | 8100 | 5273 (Vite) |

에이전트 MCP 등록(`http://127.0.0.1:8000/mcp`)은 앱(8000)을 가리킨다. 그래서 개발 서버를 띄워도
에이전트는 계속 앱을 쓴다. 8000을 다른 프로그램이 쓰고 있으면 앱이 시작 화면에 알려 준다.

## 다른 기기

앱은 이 Mac 전용이다(`127.0.0.1`에만 열림). 아이패드 등 다른 기기 접속은 아직 없다(`docs/backlog.md`).
개발할 때는 Tailscale serve로 개발 서버(5273)를 tailnet에 열어 쓰고(`tailscale serve --bg
http://127.0.0.1:5273`), 그때 캘린더 구독 링크 주소는 `backend/.env`의 `ARGOS_PUBLIC_URL`로 준다.

## 구조

- `desktop/src-tauri`: Tauri v2 셸(Rust). 서버 sidecar 시작·종료, 창, 메뉴 막대, 전역 단축키, 알림.
  자동 업데이트는 `src/update.rs`. Argos 화면이 부를 수 있는 앱 명령은 `notify`, `hide_quick`,
  `open_main`, `update_status`, `check_update`, `restart_to_update`뿐이다(`build.rs`의 목록이 권한을
  만들고 `capabilities/default.json`이 `127.0.0.1:8000`에서 온 페이지에만 준다).
- `desktop/web`: 서버가 뜨기 전의 시작 화면(오류도 여기에 표시).
- `desktop/argos-server.spec`: PyInstaller 설정. Claude SDK에 들어 있는 CLI(~200MB)는 빼고 사용자가
  설치한 `claude`를 쓴다.
- `backend/src/argos/desktop.py`: sidecar 진입점(PATH, 데이터 폴더, 마이그레이션 전 백업, uvicorn).
- `backend/src/argos/onboarding.py`: 도구 점검, MCP·스킬 설치와 제거(`/api/v1/setup*`).
- `frontend/src/pages/Welcome.tsx`: 환영 화면과 설정의 "에이전트 도구" 카드.
