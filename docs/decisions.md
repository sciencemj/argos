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
