"""Browser scene synchronization and agent endpoints."""
import contextlib
import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from agent_tools import Scene, TOOLS
from mcp_bridge import create_mcp


def install_agents(app, service):
    router = APIRouter(prefix='/api/agent')

    @router.get('/config')
    def config():
        return {'mcp_url': '/mcp/', 'tools': list(TOOLS)}

    @router.put('/sessions/{session_id}')
    def publish_scene(session_id: str, scene: Scene):
        if not 1 <= len(session_id) <= 100:
            raise HTTPException(422, 'Invalid session ID')
        return service.publish(session_id, scene)

    @router.post('/sessions/{session_id}/pairing')
    def pairing(session_id: str):
        return service.pairing_code(session_id)

    @router.websocket('/sessions/{session_id}/ws')
    async def session_socket(websocket: WebSocket, session_id: str):
        # Only same-origin browser connections may subscribe to a live scene.
        from urllib.parse import urlparse
        origin = websocket.headers.get('origin')
        if origin and urlparse(origin).netloc != websocket.headers.get('host'):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            session = service.session(session_id)
            session['ws_connected'] = True
            while True:
                await websocket.send_json({'events': service.events(session_id),
                                           'agent_connected': session.get('agent_connected', False)})
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=0.25)
                    if message.get('ack'):
                        service.acknowledge(session_id, message['ack'])
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, HTTPException):
            pass
        finally:
            if session_id in service.sessions:
                service.sessions[session_id]['ws_connected'] = False

    @router.get('/sessions/{session_id}/events')
    def events(session_id: str):
        return {'events': service.events(session_id)}

    @router.delete('/sessions/{session_id}/events/{event_id}')
    def acknowledge(session_id: str, event_id: str):
        service.acknowledge(session_id, event_id)
        return {'ok': True}

    @router.post('/tools/{name}')
    def tool(name: str, arguments: dict):
        try:
            return service.dispatch(name, arguments)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    # MCP wrappers explicitly move physics calls to a worker thread.
    mcp = create_mcp(service.dispatch)
    mcp_app = mcp.streamable_http_app()
    previous_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application):
            async with mcp.session_manager.run():
                yield

    app.router.lifespan_context = lifespan
    app.include_router(router)
    app.mount('/mcp', mcp_app)
    return mcp
