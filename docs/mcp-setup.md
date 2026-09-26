# Argos MCP 연결

Argos 서버가 켜져 있으면 `http://127.0.0.1:8000/mcp`에서 MCP(Streamable HTTP)를 제공한다. 8000은
데스크톱 앱의 포트다(`docs/desktop.md`). 개발 서버(`make dev`)는 8100이라 에이전트는 계속 앱에 연결된다.
에이전트가 이 도구로 쓴 내용은 곧바로 앱 화면에 반영되고, 채널 피드에 에이전트 이름으로 남는다.

- **호출자 표시**: URL의 `?agent=<이름>`(영문 소문자·숫자·`-`·`_`)이 활동 기록의 `agent:<이름>`이 된다.
  `X-Argos-Agent` 헤더로도 줄 수 있다. 없으면 `agent:unknown`.
- **도구 화이트리스트**: 커스텀 에이전트(설정 → 커스텀 에이전트)는 허용된 도구만 부를 수 있다. 호출자는
  `?agent=` 이름으로 판별하고, 목록에 없는 도구는 서버가 거절한다. 기본 에이전트는 모든 도구를 쓴다.
  서버에 인증이 없으므로 이 목록은 에이전트의 행동을 제한하는 장치이지 보안 경계가 아니다.
- **삭제**: `delete_task`, `delete_event`는 바로 지우지 않고 승인 요청을 만든다. 앱의 승인 카드에서
  "승인하고 삭제"를 눌러야 실행된다.
- **보안**: 서버는 `127.0.0.1`에만 바인딩되고, 다른 Host 헤더로 들어온 MCP 요청은 거부한다(DNS
  rebinding 방지). 인증은 없으므로 외부에 노출하지 않는다.

## 등록 명령 (2026-09-25, 설치된 버전의 `--help`로 확인)

앱의 설정 → 에이전트 → 에이전트 도구(또는 처음 환영 화면)에서 "Argos에 연결"을 누르면 아래 등록과
`argos` 스킬 설치를 대신 해 주고, 같은 곳에서 지울 수 있다(`docs/desktop.md`). 손으로 할 때는:

```bash
# Claude Code 2.1.x — 모든 프로젝트에서 쓰려면 --scope user
claude mcp add --transport http --scope user argos "http://127.0.0.1:8000/mcp?agent=claude"

# Codex CLI 0.156.x
codex mcp add argos --url "http://127.0.0.1:8000/mcp?agent=codex"

# Hermes Agent 0.21.x
hermes mcp add argos --url "http://127.0.0.1:8000/mcp?agent=hermes"
```

## 도구

| 구분 | 도구 |
|---|---|
| 읽기 | `list_channels`, `get_today`, `get_schedule`, `list_tasks`, `list_inbox`, `get_course_progress`, `search_notes` |
| 쓰기 | `add_task`, `update_task`, `move_task`, `create_event`, `update_event`, `capture_note`, `create_channel`(새 과목·프로젝트) |
| 승인 필요 | `delete_task`, `delete_event` |

날짜는 `YYYY-MM-DD`(할 일 마감이면 23:59, 일정이면 종일) 또는 ISO 시각. 오프셋이 없으면
Asia/Seoul로 읽는다. 채널은 이름(`#` 생략 가능) 또는 id. `search_notes`는 연결된 옵시디언 볼트의 노트를 제목·본문으로 찾는다(볼트가 없으면 빈 결과).
