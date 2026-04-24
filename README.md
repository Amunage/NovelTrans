# noveltrans

일본어 TXT 소설을 크롤링하고, 로컬 `llama.cpp` 서버로 한국어 번역하는 Windows용 콘솔 앱입니다.

## 빠른 사용법

## 지원하는 웹소설 사이트

- `syosetu.org`
  - 예시: `https://syosetu.org/novel/.../`
- `syosetu.com` 계열
  - 지원 도메인: `ncode.syosetu.com`, `novel18.syosetu.com`, `novelcom.syosetu.com`, `noc.syosetu.com`, `mnlt.syosetu.com`
  - 예시: `https://ncode.syosetu.com/n1234ab/`
- `kakuyomu.jp`
  - 예시: `https://kakuyomu.jp/works/...`
- `pixiv.net` 소설
  - 시리즈 예시: `https://www.pixiv.net/novel/series/...`
  - 개별 회차 / 단편 예시: `https://www.pixiv.net/novel/show.php?id=...`

크롤러에서 위 URL을 입력하면 사이트를 자동으로 인식합니다.

### 1. 실행 파일 배포본 사용

1. `dist/NovelTrans/` 폴더를 원하는 위치에 둡니다.
   - 앱 실행 파일은 `dist/NovelTrans/NovelTrans.exe`입니다.
   - PyInstaller 내부 파일은 `dist/NovelTrans/_internal/`에 들어갑니다.
2. 실행하면 같은 폴더의 `data/` 아래에 아래 항목이 자동 생성되거나 다운로드됩니다.
   - `.env`
   - `glossary/glossary.json`
   - `llama/`
   - `models/`
3. 메인 메뉴에서 필요한 작업을 선택합니다.
   - `[1] 추출`
   - `[2] 번역`
   - `[9] 설정`

### 2. 원문 파일 배치

기본 원문 폴더는 `source/`입니다.

폴더 구조 예시:

```text
source/
  작품이름/
    0001.txt
    0002.txt
    0003.txt
```

- 작품별로 하위 폴더를 만듭니다.
- 챕터 파일 이름은 `0001.txt`, `0002.txt` 같은 4자리 숫자 형식이어야 합니다.

### 3. 번역 결과 위치

기본 출력 폴더는 `translated/`입니다.

예시:

```text
translated/
  작품이름/
    0001_ko.txt
    0002_ko.txt
    draft/
      0001_ko_draft.txt
```

## 실행 환경

- Windows 기준 배포를 전제로 합니다.
- Python은 배포본 사용자 PC에 별도 설치할 필요가 없습니다.
- 첫 실행 시 인터넷 연결이 필요할 수 있습니다.
- NVIDIA GPU가 있으면 CUDA 대응 `llama.cpp` 런타임을 자동으로 시도합니다.
- GPU가 없어도 CPU 실행은 가능하지만, 대형 모델은 매우 느릴 수 있습니다.

## 설정

메인 메뉴의 `[9] 설정`에서 주요 값을 바꿀 수 있습니다.

자주 쓰는 항목:

- `SOURCE_PATH`: 원문 TXT 폴더
- `OUTPUT_ROOT`: 번역 결과 폴더
- `LLAMA_MODEL_PATH`: 사용할 GGUF 모델 경로
- `CTX_SIZE`: 컨텍스트 크기
- `N_PREDICT`: 최대 출력 토큰 수
- `GPU_LAYERS`: GPU에 올릴 레이어 수
- `THREADS`: CPU 스레드 수

## 개발용 빌드

가상환경이 준비된 상태에서 아래 배치 파일로 빌드합니다.

```bat
.build.bat
```

빌드가 끝나면 `dist/NovelTrans/NovelTrans.exe`와 `dist/NovelTrans/_internal/`이 생성됩니다.

## 트러블슈팅

### `[ERROR] GGUF 모델이 없습니다. 설정을 확인해주세요.`

- 모델 다운로드가 완료되지 않았거나 `LLAMA_MODEL_PATH`가 잘못된 경우입니다.
- `[9] 설정 -> [2] 모델 다운로드`를 다시 실행해 보세요.
- 또는 `data/.env`의 `LLAMA_MODEL_PATH`가 실제 파일을 가리키는지 확인하세요.

### `[ERROR] 원문 폴더가 없습니다. 설정을 확인해주세요.`

- `SOURCE_PATH`가 존재하지 않거나 폴더가 아닌 경우입니다.
- 기본값은 `source`입니다.

### `[ERROR] 번역할 원문 txt 파일이 없습니다.`

- `source/작품명/0001.txt` 같은 형식의 파일이 필요합니다.
- 작품 폴더만 있고 챕터 txt가 없으면 번역 메뉴로 진입하지 못합니다.

### 첫 실행에서 런타임 또는 모델 다운로드가 실패합니다.

- 인터넷 연결 상태를 확인하세요.
- 보안 프로그램이 다운로드 파일 생성이나 압축 해제를 막지 않는지 확인하세요.
- 앱 폴더에 쓰기 권한이 있는 위치에서 실행하세요.
- 문제가 생기면 `data/app_log.log`를 확인하세요.

### Windows Smart App Control 때문에 실행이 막힙니다.

- Windows 11에서는 서명되지 않은 앱 실행 시 Smart App Control이 차단할 수 있습니다.
- `설정 -> 개인정보 및 보안 -> Windows 보안 -> 앱 및 브라우저 컨트롤 -> Smart App Control`로 이동하세요.
- 여기서 상태를 확인하고 필요하면 끌 수 있습니다.
- 한 번 끄면 일부 환경에서는 재설치 또는 초기화 전까지 다시 켜기 어려울 수 있으니 주의하세요.

### GPU가 있는데도 느리거나 실행이 불안정합니다.

- VRAM이 부족할 수 있습니다.
- 현재 기본 후보 모델은 26B 계열이라 8GB VRAM 환경에서는 여유가 거의 없습니다.
- 이런 경우 `CTX_SIZE`, `N_PREDICT`, `MAX_CHARS`를 낮추는 편이 안전합니다.

### 작은 아이콘이 바로 안 바뀝니다.

- Windows 아이콘 캐시가 남아 있을 수 있습니다.
- 탐색기를 다시 시작하거나 로그아웃/재부팅 후 다시 확인하세요.

## 로그

- 로그 파일은 `data/app_log.log`에 생성됩니다.
- 프로그램을 새로 실행할 때마다 로그는 초기화됩니다.
