"""
설치부터 실행까지 한 번에 해 주는 도우미 (실행하기.bat이 이 파일을 실행합니다)

    python start.py

  1. 이 프로젝트 전용 설치 공간(.venv) 만들기
  2. 필요한 도구 설치             → 기록: 설치기록.txt
  3. .env 키 확인 (문제가 있으면 메모장을 열어 줌)
  4. 디스코드 연결 테스트 (처음 한 번 성공하면 다음부터 건너뜀)
  5. 뉴스레터 만들기: run.py (① 수집 → ② 선별 → ③ 요약 → ④ 검수 → ⑤ 디스코드 발행) → 기록: 실행결과.txt
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)

VENV_PY = HERE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
INSTALL_OK = HERE / ".venv" / "install_ok.txt"   # 설치가 끝까지 됐다는 표시
DISCORD_OK = HERE / ".venv" / "discord_ok.txt"   # 디스코드 테스트에 성공했다는 표시
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
INSTALL_LOG = "설치기록.txt"
RESULT_LOG = "실행결과.txt"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def run(cmd: list, log_path: str | None = None, append: bool = False) -> int:
    """명령을 실행하면서 화면과 기록 파일에 동시에 적습니다. 종료 코드를 돌려줍니다."""
    log = open(log_path, "a" if append else "w", encoding="utf-8") if log_path else None
    try:
        proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=CHILD_ENV)
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            sys.stdout.write(line)
            sys.stdout.flush()
            if log:
                log.write(line)
                log.flush()
        return proc.wait()
    except OSError as e:
        say(f"  실행하지 못했어요: {e}")
        if log:
            log.write(f"실행하지 못함: {cmd} → {e}\n")
        return 1
    finally:
        if log:
            log.close()


def stop(msg: str) -> None:
    say()
    say(f"[실패] {msg}")
    say("Claude에게 \"설치 실패했어\"라고 말해 주세요.")
    sys.exit(1)


def main() -> None:
    say("=" * 48)
    say("  AI 뉴스레터 에이전트 - 설치하고 실행하기")
    say("=" * 48)

    if sys.version_info < (3, 10):
        stop(f"파이썬 3.10 이상이 필요해요. 지금 버전: {sys.version.split()[0]}")

    # 1. 전용 설치 공간 (지난번 설치가 끝까지 안 됐으면 비우고 새로 만듦)
    say()
    if VENV_PY.exists() and INSTALL_OK.exists():
        say("[1/5] 전용 설치 공간: 이미 있어요")
        Path(INSTALL_LOG).write_text("", encoding="utf-8")
    else:
        say("[1/5] 전용 설치 공간을 만드는 중...")
        if run([sys.executable, "-m", "venv", "--clear", ".venv"], INSTALL_LOG) != 0:
            stop("설치 공간을 만들지 못했어요. 기록: 설치기록.txt")

    # 2. 도구 설치
    say()
    say("[2/5] 필요한 도구를 설치하는 중... 처음엔 1~2분 걸려요")
    if run([VENV_PY, "-m", "pip", "install", "--disable-pip-version-check",
            "-r", "requirements.txt"], INSTALL_LOG, append=True) != 0:
        stop("도구 설치에 실패했어요. 기록: 설치기록.txt")
    INSTALL_OK.write_text("ok", encoding="utf-8")

    # 3. 키 확인
    say()
    say("[3/5] .env 파일의 키를 확인하는 중...")
    env_file = HERE / ".env"
    if not env_file.exists():
        shutil.copy(HERE / ".env.example", env_file)
    if run([VENV_PY, "check_env.py"], RESULT_LOG) != 0 and os.name == "nt":
        say()
        say("  메모장이 열려요. 표시된 칸의 = 뒤에 값을 붙여 넣고,")
        say("  Ctrl+S로 저장한 다음 메모장을 닫으면 계속 진행돼요.")
        subprocess.run(["notepad.exe", str(env_file)])
        say()
        run([VENV_PY, "check_env.py"], RESULT_LOG, append=True)

    # 4. 디스코드 테스트 (한 번 성공하면 매번 테스트 메시지를 보내지 않음)
    say()
    if DISCORD_OK.exists():
        say("[4/5] 디스코드 연결: 이미 확인했어요")
    else:
        say("[4/5] 디스코드 연결 테스트...")
        if run([VENV_PY, "test_discord.py"], RESULT_LOG, append=True) == 0:
            DISCORD_OK.write_text("ok", encoding="utf-8")

    # 5. 뉴스레터 만들기: 수집 → 선별 → 요약 → 검수 → 발행 (run.py → graph.py의 LangGraph 한 번 실행)
    say()
    say("[5/5] 뉴스레터 만드는 중... 수집 → 선별 → 요약 → 검수 → 디스코드 발행 (3~5분)")
    if run([VENV_PY, "run.py"], RESULT_LOG, append=True) != 0:
        stop("뉴스레터를 만드는 중 오류가 났어요. 기록: 실행결과.txt")

    say()
    say("=" * 48)
    say("  끝! 결과가 실행결과.txt 파일에 저장됐어요.")
    say("  Claude에게 \"실행했어\"라고만 말하면 돼요.")
    say("=" * 48)


if __name__ == "__main__":
    main()
