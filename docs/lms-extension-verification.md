# LearningX 설치 도우미와 자동 갱신 검증

2026-10-09 macOS Apple Silicon에서 별도 데스크톱 빌드와 실제 Google Chrome 154.0.8037.98로 검증했다.

## 격리 환경

- 테스트 루트: `desktop/build/learningx-test-20261009/` (Git 제외).
- 앱 A: `Argos LearningX Test.app`, 확장 버전 0.2.0.
- 앱 B: `Argos LearningX Test Updated.app`, 테스트용 확장 버전 0.2.1.
- 앱 ID: `app.argos.learningxtest`, 서버: `http://127.0.0.1:18123`.
- DB와 확장 폴더는 테스트 루트의 `data/`, Chrome 프로필은 `chrome-profile/`를 사용했다.
- 앱 업데이트, 에이전트 설정 자동 반영, 주기적 외부 연동과 전역 단축키를 테스트 복사본에서 비활성화했다.
- `/Applications/Argos.app`을 교체하지 않았다. 기존 앱의 Info.plist, 실행 파일 두 개, 아이콘, 코드 서명 목록의 SHA-256을 전후 비교했다.
- 테스트용 앱은 로컬 ad-hoc 서명 빌드이며 배포용 공증 빌드는 아니다. 제품 소스의 확장 버전은 0.2.0으로 유지했다.

## 실제 Chrome 검증 결과

| 항목 | 결과 |
| --- | --- |
| 새 앱의 초기 설정 화면에서 확장 폴더 준비 | 성공, 테스트 데이터 폴더에 생성 |
| Chrome 개발자 모드와 unpacked 확장 등록 | 성공, 확장 ID `fgdkandmacnfhgpjplljflllpbaoocfg` |
| 실제 툴바 팝업에서 연결 코드 저장 | 성공, 앱에 연결 확인 시각 기록 |
| 자동 수집 끄기 / 주차자료 저장 켜기 | 저장 성공 |
| 설정 화면의 설치 안내와 폴더 경로 복사 | 성공, 실제 클립보드 값과 경로 일치 |
| 앱 B 시작 시 같은 확장 폴더 갱신 | 성공, 재등록 없이 버전 0.2.1 준비 |
| 확장의 자연스러운 5분 업데이트 알람 | 성공, 수동 reload 또는 업데이트 호출 없이 관찰 |
| 실제 worker 재시작과 새 코드 로드 | 성공, 버전 0.2.0 → 0.2.1, worker target 변경 |
| 연결 코드와 수집 옵션 유지 | 성공, 코드 해시 동일, 자동 수집 꺼짐 / 자료 저장 켜짐 유지 |
| Chrome 확장 관리의 오류 | 갱신 후 runtimeErrors 0, manifestErrors 0, ENABLED |
| 앱에 표시되는 새 연결 확인 시각 | 2026-10-09 13:05 KST |

13:02:01 KST에 버전 0.2.0 worker를 관찰하기 시작했다. 원래 알람 예정 시각은 13:05:39였고, 13:05:40에 버전 0.2.1의 새 worker와 보존된 설정을 확인했다. Chrome의 실제 `chrome.alarms`가 실행했으며 자동 수집은 계속 꺼져 있었다. 연결 코드는 출력하지 않고 해시 비교 결과만 기록했다.

테스트 루트의 `evidence/update-observation.json`, `evidence/settings.png`, `evidence/install-guide.png`로 결과를 확인했다. 앱 B에는 화면 표시로 구별하기 위한 테스트용 버전·문구 변경만 추가했다. 검증 후 사용자 요청에 따라 테스트 앱·서버·DB·Chrome 프로필·증거 파일을 정리했으며, 위 경로는 당시 환경을 설명하기 위한 기록이다. 기존 앱의 비교 대상 파일 5개는 모두 변경되지 않았다.

## 확인 중 수정한 부분과 한계

`open_lms_extension` Tauri 명령의 권한 생성 목록과 기본 capability 등록 누락을 수정했다. 수정 후 별도 앱 빌드와 Rust 테스트 2개가 통과했다. 생성된 `allow-open-lms-extension` 권한 파일도 포함한다.

OS 접근성 자동화가 거부되어 Chrome의 폴더 선택창은 직접 조작하지 못했다. Chrome의 로컬 CDP `Extensions.loadUnpacked`로 도우미가 생성한 폴더를 실제 Chrome에 등록했다. 실제 툴바 팝업에서 연결을 저장했고, 앱의 웹 화면을 실제 Chrome으로 조작했다. 앱 안의 **폴더 열기 / Chrome 확장 관리 열기** 버튼은 컴파일·권한 등록까지 확인했으며 네이티브 클릭 결과는 미검증이다.

독립 Chrome 프로필에는 학교 계정으로 새로 로그인하지 않았다. 이번 검증은 설치 도우미·연결·자동 갱신을 대상으로 하며, 새 빌드의 실학교 자료 수집 검증은 포함하지 않는다. 이전 실제 파일 수집 결과는 [자료 검증 기록](lms-materials-verification.md)을 참고한다.
