# 앱 안 변경 내역 (Phase 19 설계)

2026-10-22 사용자 요청. 캘린더 마감(Phase 18)은 별도 spec.

## 목표

자동 업데이트 뒤 무엇이 바뀌었는지 알 길이 없다(업데이트 알림 본문은 `Argos {버전}` 한 줄, GitHub 릴리스
노트는 커밋 목록 자동 생성). 사람이 읽는 한국어 변경 내역을 한 곳에 쓰고, 앱이 업데이트 뒤 한 번 보여 준다.

성공 기준:

- 업데이트 후 처음 열면 지난번 본 버전 이후의 변경 내역이 모달로 한 번 뜬다. 확인하면 다시 안 뜬다.
- 첫 설치에는 뜨지 않는다.
- 설정 → 운영 "앱 버전"에서 전체 변경 내역을 언제든 본다.
- 업데이트 알림과 GitHub 릴리스 본문이 같은 내용이다.
- 변경 내역이 없는 버전은 `make release`가 막는다.

범위 밖: 영어 번역(UI 문구 `t()` 규칙과 달리 내역 본문은 한국어만), 항목별 링크·이미지.

## 원본: `CHANGELOG.md`

저장소 루트. 형식:

```markdown
# 변경 내역

## 0.1.17 · 2026-10-22

- 칸반 마감이 폰 캘린더에 보여요.
- …
```

`## <semver> · <YYYY-MM-DD>` 제목 아래 `- ` 목록. 목록 항목 안의 인라인 마크다운(굵게, 코드)만 허용.
v0.1.16까지는 태그 사이 커밋으로 짧게 소급 작성한다.

## 프런트 (`frontend/src/changelog.ts`, `changelog.tsx`)

- `changelog.ts`: `import raw from "../../CHANGELOG.md?raw"`, `parseChangelog(raw): Release[]`
  (`{version, date, items: string[]}`, 최신 순), `compareVersions(a, b)`, `releasesAfter(list, seen,
  current)`(seen < v ≤ current). 순수 함수라 vitest로 검증.
- `changelog.tsx`: `ChangelogList`(버전·날짜·항목, 항목은 기존 `markdown.tsx` 렌더러로), `WhatsNew` 모달.
- Vite가 루트 밖 파일을 읽도록 `server.fs.allow`에 저장소 루트 추가(필요하면). 빌드에 문자열로 들어가
  백엔드·PyInstaller는 바뀌지 않는다.

## 본 버전 기록

`app_setting.changelog_seen`(서버, 폰·데스크톱 공유). API: `GET /settings/changelog` →
`{seen: str | null}`, `PUT /settings/changelog {seen}`. 비밀 값이 아니고 사용자 설정이라
`services.set_setting`(활동 기록 남김). 버전 형식(semver) 검사, 아니면 422.

## 띄우기

`layout/Shell.tsx`에서 `WhatsNew`를 렌더:

1. 현재 버전 = `updateStatus()?.current`(데스크톱). 없으면(브라우저 개발 화면) 아무것도 안 함.
2. `seen`이 null이면 조용히 `PUT seen=current`(첫 설치).
3. `releasesAfter`가 비어 있지 않으면 모달("Argos {current}에서 바뀐 점", 목록, "확인").
   확인·닫기 → `PUT seen=current`.

온보딩(환영 화면) 중에는 띄우지 않는다.

## 설정에서 보기

`OpsSettings.tsx`의 앱 버전 섹션에 "변경 내역" 버튼 → 같은 모달에 전체 목록(`parseChangelog` 전부).
데스크톱이 아니어도 보인다.

## 디자인

맞는 디자인 캔버스가 없어 기존 모달(승인·충돌 다이얼로그)과 설정 섹션 컴포넌트, `index.css` 토큰만 쓴다.
`decisions.md`에 기록. 라이트·다크 화면 확인.

## 릴리스 연결

- `make release VERSION=x.y.z`: `CHANGELOG.md`에 `## x.y.z ·` 섹션이 없으면 멈춤(안내 문구).
- `release.yml`: 태그 버전의 섹션 본문을 뽑아(작은 Python 스크립트, `scripts/` 없이 워크플로 안) `latest.json`의
  `notes`와 `gh release create --notes-file`에 사용(`--generate-notes` 대신). 섹션이 없으면 실패.

## 테스트

- vitest: 파서(여러 버전, 빈 목록, 형식 틀린 제목 무시), `compareVersions`(0.1.9 < 0.1.10),
  `releasesAfter`, `WhatsNew`(seen null → PUT만, 새 버전 → 모달, 확인 → PUT).
- pytest: changelog API(GET 기본 null, PUT, 잘못된 버전 422). `make api-types`.
- 저장소 테스트: 실제 `CHANGELOG.md`가 파서로 읽히고 최신 버전이 `tauri.conf.json` 버전 이상(vitest).

## 문서

PLAN에 Phase 19, `decisions.md`, CLAUDE.md 명령 절(릴리스 전에 CHANGELOG 갱신).
