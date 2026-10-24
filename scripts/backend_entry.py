"""Qt-free frozen service entry used by the Electron process manager."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--service-control" in sys.argv:
        sys.argv.remove("--service-control")
        from paper_agent.service_control import main

        raise SystemExit(main())
    if "--mcp-server" in sys.argv:
        sys.argv.remove("--mcp-server")
        from paper_agent.server import main

        main()
    else:
        if "--service" in sys.argv:
            sys.argv.remove("--service")
        from paper_agent.service_daemon import main

        raise SystemExit(main())
