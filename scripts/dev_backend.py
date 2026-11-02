import sys
from pathlib import Path

from uvicorn import Config, Server
from uvicorn._subprocess import get_subprocess
from uvicorn.supervisors.statreload import StatReload


class WindowsReload(StatReload):
    def should_restart(self) -> list[Path] | None:
        changes = super().should_restart()
        if self.process.exitcode is not None:
            code = self.process.exitcode or 1
            self.shutdown()
            raise SystemExit(code)
        return changes

    def restart(self) -> None:
        self.mtimes.clear()
        # Uvicorn's Windows CTRL_C_EVENT can interrupt every service in this console.
        self.process.terminate()
        self.process.join()
        self.process = get_subprocess(config=self.config, target=self.target, sockets=self.sockets)
        self.process.start()


if __name__ == "__main__":
    sys.path.insert(0, "")
    config = Config(sys.argv[1], host="127.0.0.1", port=int(sys.argv[2]), reload=True)
    server = Server(config)
    WindowsReload(config, target=server.run, sockets=[config.bind_socket()]).run()
