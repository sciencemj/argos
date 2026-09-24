# 설계 결정 로그

| 날짜 | 결정 | 이유 |
|---|---|---|
| 2026-09-24 | 3.1 스택의 "또는" 항목 확정: SQLAlchemy(SQLModel 제외), asyncio 태스크(APScheduler 제외), TanStack Query(Zustand 제외), pyright(mypy 제외), Biome(ESLint·Prettier 대체) | 에이전트가 임의로 고르지 않도록, 의존성 최소화 |
| 2026-09-24 | `watchdog` 대신 `watchfiles` | `uvicorn[standard]`에 이미 포함, async 지원 |
| 2026-09-24 | 캘린더 뷰에 FullCalendar, UI에 shadcn/ui 사용 | 주간·월간 뷰와 레이아웃 직접 구현 비용 절감 |
| 2026-09-24 | MCP는 Streamable HTTP로 FastAPI 앱에 마운트 | domain 서비스를 같은 프로세스에서 공유, 프로세스 하나 |
| 2026-09-24 | 인박스 분류 provider는 `ollama`, `hermes`만 구현 (`openai` SDK + `base_url`). 클라우드 API provider는 보류 | 사용자 결정. 설정으로 교체 가능하게 유지 |
| 2026-09-24 | Phase 9, 10은 원안 유지 | 사용자 결정 |
