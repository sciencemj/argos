# Hermes 연결

Hermes 게이트웨이(`hermes gateway`, launchd로 상시 실행) 안의 **API 서버 플랫폼**으로 Argos와
Hermes를 잇는다. 디스코드·슬랙 연결과 같은 게이트웨이의 한 플랫폼이다. Hermes Agent 0.21.x
문서(`website/docs/user-guide/features/api-server.md`, `skills.md`, `mcp.md`) 기준.

두 방향이 있다.

| 방향 | 방법 |
|---|---|
| Argos → Hermes (`@hermes`, `/ask`, DM) | Responses API `POST /v1/responses`, `conversation`=Argos 스레드/DM 이름 |
| Hermes → Argos (일정 기록·조회) | Hermes에 Argos MCP 등록 + `argos` 스킬 |

## 1. API 서버 켜기

`~/.hermes/.env`:

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=<긴 임의 문자열>
```

`backend/.env`(git 제외):

```bash
ARGOS_HERMES_API_KEY=<같은 값>
```

```bash
hermes gateway restart   # 127.0.0.1:8642, 키 없으면 401
```

Argos는 스레드마다 `conversation: "argos-thread-<id>"`, DM은 `"argos-dm-<channel id>"`로 부른다.
Hermes가 디스코드 스레드처럼 대화별 세션(도구 호출 기록 포함)을 들고 있으므로, Argos는 Hermes가
마지막으로 답한 뒤의 새 메시지만 보낸다.

## 2. Hermes가 Argos에 기록하게 하기 (PLAN P2)

`~/.hermes/config.yaml`:

```yaml
skills:
  external_dirs:
  - /Users/sciencemj/dev/argos/integrations/hermes/skills   # argos 스킬 (저장소에서 버전 관리)

mcp_servers:
  argos:
    url: "http://127.0.0.1:8000/mcp?agent=hermes"
```

```bash
hermes gateway restart
hermes mcp list      # argos ✓ enabled
hermes skills list   # argos enabled
```

- `integrations/hermes/skills/argos/SKILL.md`가 "일정·할 일은 메모리가 아니라 Argos 도구로"라는
  규칙과 도구 사용법을 담는다. SOUL.md(페르소나)는 건드리지 않는다.
- `hermes mcp add`는 도구 활성화를 대화형으로 묻기 때문에, 자동화할 때는 위처럼 설정 파일에 쓴다.

## 되돌리기

`~/.hermes/.env.bak.argos-*`, `~/.hermes/config.yaml.bak.argos-*`로 복원하거나 위 항목을 지우고
`hermes gateway restart`.

## 보안

- API 서버와 Argos 모두 `127.0.0.1`에만 바인딩한다. 외부 노출 금지 (PLAN §8.1).
- Hermes는 터미널·파일 도구를 가진다. Argos는 사용자가 쓴 대화만 보내고, 외부 텍스트를 지시문처럼
  섞지 않는다.
- 키는 `.env`에만 둔다. 로그·WebSocket·화면에 출력하지 않는다.
