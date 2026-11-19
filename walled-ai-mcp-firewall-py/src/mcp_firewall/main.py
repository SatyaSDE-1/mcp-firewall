# # /Users/anurag/task/walled-ai-mcp-firewall-py/src/mcp_firewall/main.py
# """Entry point: Tk popups on the main thread, asyncio servers on a worker thread."""
# import asyncio
# import logging
# import threading

# import uvicorn

# from . import config
# from .approval import ApprovalService
# from .approval_socket import ApprovalSocketGateway
# from .audit import AuditLog
# from .interceptor import Interceptor
# from .server import create_firewall_app, create_remote_app
# from .ui import PopupUI


# def main() -> None:
#     logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
#     log = logging.getLogger("bootstrap")

#     loop = asyncio.new_event_loop()

#     ui = PopupUI(config.APPROVAL_TIMEOUT_S)
#     service = ApprovalService(config.APPROVAL_TIMEOUT_S)
#     audit = AuditLog(config.AUDIT_DIR, config.AUDIT_MAX_PARAMS_CHARS)
#     socket_gateway = ApprovalSocketGateway(service, config.TOKEN, loop)

#     service.on_created(ui.post_request)
#     service.on_created(socket_gateway.on_created)
#     service.on_resolved(ui.post_resolved)
#     service.on_resolved(socket_gateway.on_resolved)
#     service.on_resolved(audit.write)

#     interceptor = Interceptor(service, config.GATED_METHODS)

#     def mk(app, port):  # loopback only
#         return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

#     firewall_app = create_firewall_app(interceptor)
#     socket_gateway.mount(firewall_app)
#     servers = [mk(firewall_app, config.PORT)]
#     log.info("Firewall on 127.0.0.1:%s", config.PORT)
#     targets, secret = config.remote_targets()
#     if targets:
#         servers.append(mk(create_remote_app(interceptor, targets, secret), config.REMOTE_PORT))
#         log.info("Claude Web proxy on 127.0.0.1:%s for: %s", config.REMOTE_PORT, ", ".join(targets))
#     for s in servers:
#         s.install_signal_handlers = lambda: None  # not the main thread

#     def runner():
#         asyncio.set_event_loop(loop)
#         ui.decider = lambda rid, allow: loop.call_soon_threadsafe(service.decide, rid, allow)
#         loop.run_until_complete(asyncio.gather(*(s.serve() for s in servers)))

#     t = threading.Thread(target=runner, daemon=True)
#     t.start()

#     def shutdown():
#         service.shutdown()  # deny everything still pending
#         for s in servers:
#             s.should_exit = True

#     try:
#         ui.run()
#     finally:
#         loop.call_soon_threadsafe(shutdown)
#         t.join(timeout=5)
#         log.info("Stopped; pending approvals were denied")


# if __name__ == "__main__":
#     main()


"""Application entry point.

Runs the FastAPI/Uvicorn servers on a background thread.
The Electron application provides the approval UI.
"""

import asyncio
import logging
import signal
import threading

import uvicorn

from . import config
from .approval import ApprovalService
from .approval_socket import ApprovalSocketGateway
from .audit import AuditLog
from .interceptor import Interceptor
from .server import create_firewall_app, create_remote_app


log = logging.getLogger("bootstrap")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Create the asyncio event loop.
    loop = asyncio.new_event_loop()

    # Create approval and audit services.
    service = ApprovalService(config.APPROVAL_TIMEOUT_S)

    audit = AuditLog(
        config.AUDIT_DIR,
        max_params_chars=config.AUDIT_MAX_PARAMS_CHARS,
    )

    # Create Socket.IO gateway for the Electron approval UI.
    socket_gateway = ApprovalSocketGateway(
        service,
        config.TOKEN,
        loop,
    )

    # Connect approval events to Socket.IO and audit logging.
    service.on_created(socket_gateway.on_created)
    service.on_resolved(socket_gateway.on_resolved)
    service.on_resolved(audit.write)

    # Create the interceptor.
    interceptor = Interceptor(
        service,
        config.GATED_METHODS,
    )

    # Create the local firewall application.
    firewall_app = create_firewall_app(interceptor)
    firewall_app = socket_gateway.mount(firewall_app)

    def create_server(app, port: int) -> uvicorn.Server:
        return uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        )

    servers = [
        create_server(firewall_app, config.PORT),
    ]

    # Start the optional Claude Web proxy when configured.
    targets, secret = config.remote_targets()

    if targets:
        remote_app = create_remote_app(
            interceptor,
            targets,
            secret,
        )

        servers.append(
            create_server(remote_app, config.REMOTE_PORT)
        )

        log.info(
            "Claude Web proxy configured on port %s",
            config.REMOTE_PORT,
        )

    # Uvicorn signal handlers must not run in the worker thread.
    for server in servers:
        server.install_signal_handlers = lambda: None

    stop_event = threading.Event()

    def shutdown_handler(signum, frame) -> None:
        log.info("Shutdown signal received: %s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    def runner() -> None:
        asyncio.set_event_loop(loop)

        async def serve() -> None:
            await asyncio.gather(
                *(server.serve() for server in servers)
            )

        try:
            loop.run_until_complete(serve())

        except Exception:
            log.exception("Firewall server stopped unexpectedly")

        finally:
            stop_event.set()

            if not loop.is_closed():
                loop.close()

    server_thread = threading.Thread(
        target=runner,
        name="firewall-server",
        daemon=True,
    )

    server_thread.start()

    log.info(
        "Firewall running at http://127.0.0.1:%s",
        config.PORT,
    )
    log.info(
        "Socket.IO endpoint: http://127.0.0.1:%s/approvals/socket.io",
        config.PORT,
    )
    log.info("Waiting for Electron approval UI connection")

    try:
        # Keep the main thread alive until shutdown is requested.
        stop_event.wait()

    finally:
        def shutdown() -> None:
            # Deny all approvals that are still pending.
            service.shutdown()

            # Ask Uvicorn servers to stop.
            for server in servers:
                server.should_exit = True

        if loop.is_running():
            loop.call_soon_threadsafe(shutdown)

        server_thread.join(timeout=10)

        log.info("Firewall stopped")


if __name__ == "__main__":
    main()
