<div align="center">

<img src="docs/images/icon.png" width="112" alt="Argos 앱 아이콘" />

# Argos

**학교 공부와 사이드 프로젝트를 한곳에서. 일정, 할 일, 노트, AI 에이전트가 모이는 내 Mac 전용 대시보드.**

[English](README.md) · **한국어**

[![CI](https://github.com/sciencemj/argos/actions/workflows/ci.yml/badge.svg)](https://github.com/sciencemj/argos/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/sciencemj/argos?label=release&color=f08a4b)](https://github.com/sciencemj/argos/releases/latest)
![macOS](https://img.shields.io/badge/macOS-13%2B%20·%20Apple%20silicon-111118?logo=apple&logoColor=white)
![Tauri](https://img.shields.io/badge/Tauri-2-24C8DB?logo=tauri&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-19-149ECA?logo=react&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-server-6E56CF)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/today-dark.png" />
  <img src="docs/images/today.png" alt="Argos 오늘 화면" width="100%" />
</picture>

</div>

과목과 프로젝트마다 채널이 하나씩 생기고, 채널에는 채팅처럼 쓰는 피드와 칸반, 캘린더가 붙어 있어요.
친구에게 메시지 보내듯 "금요일에 퀴즈", "회의 전에 API 초안 공유하기"라고 적으면 Argos가 할 일·일정·노트로
나눠 정리해요. Hermes, Claude Code, Codex, 로컬 모델 같은 AI 에이전트도 같은 공간에서 일해요. 스레드에서
답하고, 카드에 걸린 코딩 잡을 실행하고, Argos의 MCP 서버로 일정을 읽고 써요.

모든 것이 내 Mac 안에서 돌아요. 앱은 `127.0.0.1`에만 열리고, 데이터는
`~/Library/Application Support/Argos`에, 비밀 값은 macOS 키체인에 둬요.

## 기능

**정리하기**
- **과목·프로젝트별 채널**을 영역으로 묶고, **오늘**과 **내 공간**이 기본으로 있어요. 내 공간의 수집함에서 입력을 정리하고, 개인 할 일·일정·노트를 이어서 봐요.
- **피드 중심 입력**: 그냥 쓴 메시지는 인박스로 가고 로컬 모델(Ollama)이나 Hermes가 할 일·일정·노트로
  분류해요. 슬래시 명령(`/task`, `/event`, `/note`, `/ask`, `/job`, `/debate`)을 쓰면 바로 만들어져요.
- **칸반**(끌어서 이동, 진행 중 개수 경고), 반복 일정까지 보이는 **캘린더**(주·월), 마감·일정·인박스·
  매일 루틴을 모은 **오늘** 화면.
- **옵시디언 볼트**: 전문 검색, 채널별 노트, 체크박스 할 일 양방향 동기화(Tasks·Dataview 표기). 노트를
  고치기 전에는 항상 백업해요.
- **캘린더 연동**: 캘린더 앱에서 구독하는 ICS 피드, iCloud(CalDAV) 양방향 동기화.
- **알림과 주간 리뷰**: 마감, 오래 둔 인박스를 앱 안, macOS 알림, Hermes를 통한 메신저로 알려 주고,
  매주 과목별 숫자를 담은 리뷰를 **리뷰** 화면에 쌓아요.

**에이전트와 일하기**
- **Hermes, Claude Code, Codex, 로컬 모델**과 스레드·1:1 대화. `@이름`으로 아무나 부를 수 있어요.
- **코딩 잡**: 카드에서 `/job`을 쓰면 허용한 폴더 안에서 Claude Code나 Codex가 작업하고, 결과를 카드에
  남겨요(할 일 → 진행 중 → 검토).
- **커스텀 에이전트**(프롬프트·모델·허용 도구를 정하고 YAML로 가져오기·내보내기)와 사회자가 있는
  에이전트끼리의 **토론**.
- **MCP 서버**(`/mcp`): 에이전트가 채널 조회, 할 일·일정 추가, 메모 남기기, 새 과목·프로젝트 채널 만들기를
  할 수 있어요. 삭제는 사용자가 승인해야 실행돼요.
- 상태바에서 **Claude·Codex 요금제 사용량**을 보여 줘요.

**데스크톱 앱**
- 메뉴 막대 앱이라 창을 닫아도 동기화·잡·알림이 계속 돌아요.
- 어디서든 <kbd>⌘</kbd><kbd>⇧</kbd><kbd>Space</kbd>로 **빠른 입력**, 바로 인박스로 들어가요.
- **처음 설정 화면**이 설치된 에이전트 도구를 찾아 한 번에 연결해요(MCP + `argos` 스킬).
- **자동 업데이트**. 앱에 들어 있는 공개 키로 서명을 확인한 것만 설치해요.
- **완전 삭제**: 에이전트 도구에 넣은 설정, 로그인 항목, 키체인 항목까지 한 번에 정리해요.

## 스크린샷

| 채널 피드 | 칸반 |
|---|---|
| ![할 일·일정·노트 카드가 붙은 피드](docs/images/feed.png) | ![프로젝트 칸반 보드](docs/images/kanban.png) |
| **캘린더** | **설정** |
| ![주간 캘린더(다크 테마)](docs/images/calendar.png) | ![에이전트와 모델 설정](docs/images/settings.png) |
| **처음 설정** | **빠른 입력** (<kbd>⌘</kbd><kbd>⇧</kbd><kbd>Space</kbd>) |
| ![처음 설정: 에이전트 도구 연결](docs/images/welcome.png) | ![빠른 입력 창](docs/images/quick.png) |

## 설치

1. [최신 릴리스](https://github.com/sciencemj/argos/releases/latest)에서 `Argos_<버전>_aarch64.dmg`를 받아요.
2. dmg를 열고 **Argos**를 **응용 프로그램** 폴더로 끌어 놓아요.
3. 처음 한 번은 **Argos를 우클릭 → 열기**로 실행해요. 유료 Apple 개발자 인증서가 아닌 ad-hoc 서명이라
   더블클릭하면 "확인되지 않은 개발자" 경고가 떠요.
4. 환영 화면을 따라가요: 도구 점검 → 연결 → 기본값 선택 → 로그인 시 자동 실행.

업데이트는 알아서 와요. 6시간마다 GitHub Releases를 확인해 새 버전을 백그라운드에서 설치해 두고, 다음에
다시 시작할 때 적용해요. 메뉴 막대에서 바로 다시 시작할 수도 있어요.

### 함께 쓰면 좋은 도구

Argos만으로도 쓸 수 있고, 도구를 더하면 기능이 늘어나요.

| 도구 | 더해지는 기능 | 설치 |
|---|---|---|
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | 대화·코딩 잡 에이전트, 사용량 표시 | `npm install -g @anthropic-ai/claude-code` |
| [Codex](https://github.com/openai/codex) | 대화·코딩 잡 에이전트, 사용량 표시 | `npm install -g @openai/codex` |
| Hermes Agent | 기본 대화 에이전트, 메신저 알림 | [docs/hermes-setup.md](docs/hermes-setup.md) 참고 |
| [Ollama](https://ollama.com/download) | 인박스 자동 분류, 비슷한 메모 묶기 | 설치 후 설정에서 모델 선택 |
| [Obsidian](https://obsidian.md) | 채널별 노트와 체크박스 할 일 | 설정에서 볼트 선택 |

### 에이전트 연결

**설정 → 에이전트 → 에이전트 도구 → "Argos에 연결"**을 누르면 각 도구의 CLI로 Argos MCP 서버를 등록하고,
언제 어떤 도구를 쓰는지 알려 주는 `argos` 스킬을 설치해요. 같은 카드에서 둘 다 다시 지울 수 있어요.

| 도구 | MCP | 스킬 |
|---|---|---|
| Claude Code | `claude mcp add --transport http --scope user argos …` | `~/.claude/skills/argos` |
| Codex | `codex mcp add argos --url …` | `~/.codex/skills/argos` |
| Hermes | `hermes config set mcp_servers.argos.url …` | `skills.external_dirs`에 추가 |

업데이트 뒤에는 Argos가 설치한 스킬과 MCP 주소만 새로 고치고, 사용자가 지운 것은 다시 넣지 않아요.

## 개인정보와 보안

- 서버는 `127.0.0.1:8000`에서만 열려요. 네트워크에는 아무것도 노출하지 않아요.
- 데이터는 `~/Library/Application Support/Argos`에 있어요(SQLite, 매일 백업).
- iCloud 앱 전용 암호는 키체인에, Hermes 키는 Hermes의 `.env`에서 읽기만 하고, Claude·Codex는 각자의
  로그인을 써요. OAuth 토큰은 저장하거나 기록하지 않아요.
- 글이 Mac 밖으로 나가는 건 사용자가 고른 에이전트와 모델을 쓸 때뿐이에요. Ollama의 `*-cloud` 모델은
  설정 화면에 따로 표시해요.
- 에이전트는 아무것도 바로 지울 수 없어요. MCP로 들어온 삭제는 승인 요청이 돼요.

## 개발

필요한 것: [uv](https://docs.astral.sh/uv/), [bun](https://bun.sh). 데스크톱 앱을 빌드하려면 Rust(rustup)와
Xcode 명령행 도구도 필요해요.

```bash
make install      # uv sync + bun install
make dev          # 백엔드 :8100 + 프런트엔드 :5273 (Vite가 /api, /ws를 프록시)
make test         # pytest + vitest
make lint         # ruff, pyright(strict), biome, tsc
make api-types    # API 모델을 바꾼 뒤 frontend/src/api-types.ts 다시 생성
make app          # 데스크톱 앱 → desktop/build/Argos.app, Argos.dmg
make release VERSION=x.y.z   # 버전 올리고 태그·푸시 → CI가 빌드·서명·배포
```

포트 8000은 설치된 앱 몫이에요(에이전트 MCP 등록이 여기를 가리켜요). 그래서 개발 서버는 8100/5273을 쓰고,
둘을 동시에 켤 수 있어요. 개발용 데이터는 `backend/data`에 있어요.

### 구조

```
backend/    FastAPI + async SQLAlchemy(SQLite WAL), Alembic 마이그레이션
  argos/services.py    유일한 쓰기 경로: 활동 기록 + WebSocket 이벤트
  argos/chat.py        메시지 라우팅: 스레드, @멘션, 슬래시 명령, 인박스
  argos/runner.py      에이전트 실행과 코딩 잡, WebSocket 스트리밍
  argos/agents.py      어댑터: Hermes, Claude Agent SDK, Codex app-server, Ollama
  argos/mcp_server.py  에이전트가 쓰는 MCP 서버
  argos/desktop.py     데스크톱 앱 안에서 도는 서버의 진입점
frontend/   React 19 + Vite + Tailwind v4 + TanStack Query
desktop/    Tauri 2 셸: 서버 실행, 메뉴 막대, 빠른 입력, 업데이트
integrations/  에이전트 도구에 설치하는 argos 스킬
```

더 자세한 내용은 [docs/PLAN.md](docs/PLAN.md)(설계와 단계), [docs/desktop.md](docs/desktop.md)(앱, 업데이트,
릴리스), [docs/mcp-setup.md](docs/mcp-setup.md)(MCP 도구), [docs/decisions.md](docs/decisions.md)(진행하며
내린 결정)에 있어요.
