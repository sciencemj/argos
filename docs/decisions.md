# 설계 결정 로그

| 날짜 | 결정 | 이유 |
|---|---|---|
| 2026-09-24 | 3.1 스택의 "또는" 항목 확정: SQLAlchemy(SQLModel 제외), asyncio 태스크(APScheduler 제외), TanStack Query(Zustand 제외), pyright(mypy 제외), Biome(ESLint·Prettier 대체) | 에이전트가 임의로 고르지 않도록, 의존성 최소화 |
| 2026-09-24 | `watchdog` 대신 `watchfiles` | `uvicorn[standard]`에 이미 포함, async 지원 |
| 2026-09-24 | 캘린더 뷰에 FullCalendar, UI에 shadcn/ui 사용 | 주간·월간 뷰와 레이아웃 직접 구현 비용 절감 |
| 2026-09-24 | MCP는 Streamable HTTP로 FastAPI 앱에 마운트 | domain 서비스를 같은 프로세스에서 공유, 프로세스 하나 |
| 2026-09-24 | 인박스 분류 provider는 `ollama`, `hermes`만 구현 (`openai` SDK + `base_url`). 클라우드 API provider는 보류 | 사용자 결정. 설정으로 교체 가능하게 유지 |
| 2026-09-24 | Phase 9, 10은 원안 유지 | 사용자 결정 |
| 2026-09-24 | 프론트 패키지 관리자는 `pnpm` 대신 `bun` (설치·스크립트 실행만). 번들러·테스트는 Vite·vitest 유지 | pnpm 미설치, bun 설치됨. `bun test`는 vitest와 API·jsdom 설정이 달라 사용 안 함 |
| 2026-09-24 | 테스트 HTTP 클라이언트는 `httpx` 대신 `httpx2` | 현재 Starlette `TestClient`가 `httpx` 사용을 deprecated 처리하고 `httpx2`를 권장 |
| 2026-09-24 | `db/` 패키지 대신 `db.py` 단일 모듈로 시작 | Phase 0에는 엔진·세션만 있음. 커지면 분리 |
| 2026-09-24 | health 경로는 `GET /api/v1/health` | 7.1절 `/api/v1` prefix 규칙에 맞춤 |
| 2026-09-24 | 마이그레이션은 앱 시작 시 자동 실행하지 않고 `make migrate`(`make dev`가 먼저 실행) | 스키마 변경을 명시적으로 |
| 2026-09-24 | `make dev`는 `kill 0` 대신 자식 job만 종료 | `kill 0`은 make를 호출한 상위 셸까지 같은 프로세스 그룹이면 종료시킴 |
| 2026-09-24 | 종일 일정은 `start_date`/`end_date`(Date, 끝 미포함) 컬럼으로 저장, `all_day`는 파생값 | 8.2 "시간대 변환으로 하루 밀리는 버그" 방지. iCalendar DATE 값과 같은 형태 |
| 2026-09-24 | ID는 UUID4 문자열 | Python 3.13 표준 라이브러리만으로 충분. 정렬은 created_at 사용 |
| 2026-09-24 | 채널별 조회는 `/channels/{id}/tasks` 대신 `GET /tasks?channel_id=`, `GET /events?start&end&channel_id=` | 엔드포인트 중복 제거. 캘린더 전체 조회와 같은 API |
| 2026-09-24 | 커서 페이지네이션은 인박스 목록에만 적용 | task는 칸반이 채널 전체를 한 번에 필요로 하고, event는 기간으로 제한됨 |
| 2026-09-24 | 과목 채널 목록은 `backend/seed.toml`(gitignore)에서 시작 시 멱등 반영, 예시는 `seed.example.toml` | §9 "설정으로 뺄 것". 파일이 없으면 `today`, `inbox`만 생성 |
| 2026-09-24 | actor 문자열 규칙: `user`, `system`, 에이전트는 `agent:<id>` (Phase 4부터) | activity_log 추적용 |
| 2026-09-24 | 칸반 재번호 시 다른 카드들의 position 변경은 activity_log에 남기지 않음 | 내부 정렬 유지 작업이며 사용자 의미가 없음. 이동한 카드의 `moved`만 기록 |
| 2026-09-24 | API 응답의 시각은 입력 오프셋과 관계없이 UTC(`Z`)로 반환 | 저장 형식과 응답 형식 일치 |
| 2026-09-24 | UI는 사용자가 준 디자인(`docs/design/*.dc.html`)을 따름. 토큰은 라이트·다크 CSS 변수, OS 설정 + 수동 전환(시스템→라이트→다크) | 사용자 결정 |
| 2026-09-24 | shadcn/ui 제외. Tailwind v4 + 직접 만든 소수 부품, 다이얼로그는 네이티브 `<dialog>` | 디자인이 고유 스타일이라 기본 모양을 덮어쓰는 비용이 더 큼 |
| 2026-09-24 | FullCalendar는 v6으로 고정 | v7(core/react)은 headless 구조로 바뀌어 v6 플러그인(daygrid 등)과 섞을 수 없음 |
| 2026-09-24 | 시드는 빈 DB(첫 실행)에만 적용, 이후 영역·채널은 앱에서 추가·삭제. 기본 시드 경로는 `seed.example.toml`(데모 과목) | 사용자가 지운 채널이 재시작 때 되살아나지 않게 |
| 2026-09-24 | 채널 삭제는 할 일·일정이 남아 있으면 409, `force=true`로만 삭제(포함 객체도 각각 activity_log 기록). 영역은 비어 있어야 삭제. 시스템 채널은 변경·삭제 불가 | 되돌리기 어려운 삭제에 확인 단계 |
| 2026-09-24 | WebSocket 이벤트는 커밋 후 도메인 서비스(`_create/_update/_delete`)에서 발행. 프론트는 이벤트를 받으면 해당 쿼리를 다시 가져옴(데이터를 직접 합치지 않음), 재연결 시 전체 재조회 | 단순하고 이벤트 유실에 강함 (§7.2) |
| 2026-09-24 | Home의 "과목별 진행"은 드래그 가능한 칸반이 아니라 상태별 개수 요약(디자인 그대로). 드래그 칸반은 채널 탭에 | 디자인 우선. PLAN Phase 2의 "과목별 스윔레인(Home)" 문구와 다름 |
| 2026-09-24 | 칸반 충돌 판정은 `pointerWithin` 우선, 없으면 `closestCorners` | 세로로 긴 컬럼에서 corner 거리만 쓰면 다른 컬럼으로 옮겨지지 않음 (E2E에서 발견) |
| 2026-09-24 | 캘린더는 브라우저 로컬 시간대로 표시(FullCalendar 이름 시간대 플러그인 미사용) | 로컬 우선 앱이라 브라우저 시간대 = 설정 시간대. 다르면 캘린더만 어긋남 |
| 2026-09-24 | `GET /tasks/{id}/activity` 추가 | 카드 상세의 "발자국"(디자인) 표시용 |
| 2026-09-24 | 디자인 중 뒤 Phase 기능(에이전트, 승인, 사용량, 출처 꼬리표, 제안 카드, 메시지 입력)은 Phase 2에서 표시하지 않음 | PLAN 0장 규칙 2 |
| 2026-09-24 | 칸반 이동의 낙관적 캐시 갱신은 드롭과 같은 렌더에서 동기적으로 수행하고, 진행 중인 재조회는 되돌리지 않고 취소 | 비동기 `onMutate`면 드롭 직후 한 프레임 동안 옛 순서가 보여 카드가 튐 |
