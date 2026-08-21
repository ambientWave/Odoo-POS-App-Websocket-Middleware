import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from typing import List
import logging
import os
import json
import asyncio
import redis.asyncio as redis
from pydantic import BaseModel


# Logging
_logger = logging.getLogger("gunicorn.error")


# Models
class NewInvoicePayload(BaseModel):
    invoice_id: int
    invoice_number: str | None = None
    amount: float
    currency: str
    


class PaymentCallback(BaseModel):
    pos_order: str
    transaction_reference: str
    status: str
    newleap_token: str


class PosOrderPayload(BaseModel):
    user_id: int
    pos_reference: str
    amount: float
    currency: str

app = FastAPI(title="Secure Middle Service")

@app.get("/")
async def health_check():
    return {"status": "ok"}

ODOO_BASE_URL = os.getenv("ODOO_BASE_URL")
NEWLEAP_TOKEN = os.getenv("NEWLEAP_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

class ConnectionManager:
    """Manages active WebSocket connections across multiple Gunicorn workers using Redis."""
    def __init__(self):
        # Local connections in this process: identifier -> WebSocket (Keys are strings)
        self.local_connections: dict[str, WebSocket] = {}
        # WebSocket ID -> list of identifiers (to cleanup on disconnect)
        self.ws_to_ids: dict[int, list[str]] = {}
        self.redis_client = None

    async def init_redis(self):
        self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        # Start background listener for cross-process messages
        asyncio.create_task(self._redis_listener())

    async def connect(self, identifiers: list, websocket: WebSocket):
        """Accepts a new connection, closes existing ones for these IDs, and stores new mappings."""
        if not identifiers:
            _logger.warning("Attempted to connect with no identifiers")
            return

        # Ensure all identifiers are strings
        identifiers = [str(i) for i in identifiers if i]

        # 1. Close existing connections for these identifiers
        for identifier in identifiers:
            await self._close_local_connection(identifier)
            # Signal other workers to kick
            if self.redis_client:
                await self.redis_client.publish("websocket_messages", json.dumps({
                    "type": "kick_user",
                    "token": identifier,
                    "exclude_id": id(websocket)
                }))

        # 2. Store new mappings
        ws_id = id(websocket)
        self.ws_to_ids[ws_id] = identifiers
        for identifier in identifiers:
            self.local_connections[identifier] = websocket
            _logger.info("Mapped identifier %s to local worker", identifier)

    async def _close_local_connection(self, identifier):
        """Closes the local connection for a specific identifier if it exists."""
        str_id = str(identifier)
        websocket = self.local_connections.get(str_id)
        if websocket:
            ws_id = id(websocket)
            try:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            except Exception:
                pass
            
            # Clean up all mappings for this WS
            ids_to_remove = self.ws_to_ids.get(ws_id, [])
            for rid in ids_to_remove:
                if rid in self.local_connections:
                    del self.local_connections[rid]
            if ws_id in self.ws_to_ids:
                del self.ws_to_ids[ws_id]
            _logger.info("Closed local connection for identifier %s (and associated IDs)", str_id)

    def disconnect(self, websocket: WebSocket):
        """Removes all mappings for a specific WebSocket."""
        ws_id = id(websocket)
        identifiers = self.ws_to_ids.get(ws_id, [])
        for identifier in identifiers:
            if identifier in self.local_connections:
                del self.local_connections[identifier]
        if ws_id in self.ws_to_ids:
            del self.ws_to_ids[ws_id]
        _logger.info("Disconnected WebSocket with identifiers %s", identifiers)

    async def send_redis_message(self, identifier, message: dict):
        """Publishes a message to Redis keyed by an identifier (token or user_id)."""
        str_id = str(identifier)
        if self.redis_client:
            payload = {
                "type": "personal_message",
                "token": str_id,
                "message": message
            }
            await self.redis_client.publish("websocket_messages", json.dumps(payload))
            _logger.info("Published message for ID %s to Redis", str_id)
        else:
            await self._deliver_locally(str_id, message)

    async def _deliver_locally(self, identifier, message: dict):
        """Sends message directly to a locally connected user by any registered ID."""
        str_id = str(identifier)
        websocket = self.local_connections.get(str_id)
        if websocket:
            try:
                await websocket.send_json(message)
            except Exception as e:
                _logger.error("Failed to send local message to %s: %s", str_id, e)
                self.disconnect(websocket)

    async def _redis_listener(self):
        """Listens for messages from other workers via Redis Pub/Sub."""
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe("websocket_messages")
        _logger.info("Started Redis Pub/Sub listener for websocket_messages")
        
        async for message in pubsub.listen():
            _logger.debug("Received message from Redis: %s", message)
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    msg_type = data.get("type")
                    target_id = data.get("token")
                    
                    if msg_type == "kick_user" and target_id:
                        exclude_id = data.get("exclude_id")
                        current_ws = self.local_connections.get(str(target_id))
                        if current_ws and id(current_ws) != exclude_id:
                            await self._close_local_connection(target_id)
                    elif msg_type == "personal_message" and target_id:
                        msg_content = data.get("message")
                        await self._deliver_locally(target_id, msg_content)
                except Exception as e:
                    _logger.error("Error in Redis listener: %s", e)

manager = ConnectionManager()

@app.on_event("startup")
async def startup_event():
    await manager.init_redis()

async def verify_credentials(token: str) -> dict:
    """
    Sends an HTTP POST request to the remote verification service.
    Returns the full JSON response dictionary.
    """
    try:
        _logger.info(f"Verifying credentials")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{ODOO_BASE_URL}/api/verify_token",
                json={"token": token},
                headers={"Authorization": f"Bearer {token}"}
            )
            data = resp.json()
            _logger.info(f"Verifying credentials | Data: {data}")
            return data
    except Exception as e:
        _logger.error(f"Verification request failed: {e}")
        return {"result_type": "error", "message": str(e)}

async def handle_payment_callback(event_data: dict):
    """Handle payment callback event from mobile"""
    payload = event_data.get("payload", {})

    pos_order = payload.get("pos_order")
    transaction_reference = payload.get("transaction_reference")
    status = payload.get("status")
    message = payload.get("message")
    error_message = payload.get("error_message")
    detail = payload.get("detail")
    newleap_token = payload.get("newleap_token")

    if newleap_token != NEWLEAP_TOKEN:
        _logger.warning("❌ Invalid NewLeap token in payment callback")
        return {"result_type": "error", "detail": "Invalid NewLeap token"}

    if not status:
        _logger.info("⏭️ Payment callback missing status")
        return {"result_type": "error", "detail": "Missing status"}

    _logger.info(
        "💳 Payment callback | PosOrder=%s | Tx=%s | Status=%s",
        pos_order, transaction_reference, status
    )

    # Send to Odoo
    try:
        _logger.info("⏭️ Payment callback | payload=%s", payload)
        payload_to_odoo = {
            "pos_order": pos_order,
            "transaction_reference": transaction_reference,
            "status": status,
        }
        if message is not None:
            payload_to_odoo["message"] = message
        if error_message is not None:
            payload_to_odoo["error_message"] = error_message
        if detail is not None:
            payload_to_odoo["detail"] = detail
        async with httpx.AsyncClient(timeout=7) as client:
            resp = await client.post(
                f"{ODOO_BASE_URL}/api/newleap/payment",
                json=payload_to_odoo,
                )
            data = resp.json()
            _logger.info("Payment callback sent to Odoo | Data=%s", data)
    except Exception as e:
        _logger.error("❌ Failed to send payment to Odoo: %s", e)

    # Broadcast to other clients
    # for connection in manager.active_connections:
    #     try:
    #         await connection.send_json({
    #             "type": "payment.paid",
    #             "payload": {
    #                 "pos_order": pos_order,
    #                 "transaction_reference": transaction_reference,
    #             }
    #         })
    #     except Exception as e:
    #         _logger.error("❌ Failed to send payment to client: %s", e)

    return {"result_type": "success"}


async def get_products(event_data: dict):
    """Handle get.products event from mobile"""
    payload = event_data.get("payload", {})
    page_number = payload.get("page_number", 1)
    token = event_data.get("token")

    _logger.info("📦 Fetching products | Page=%s", page_number)

    try:
        async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {token}"}) as client:
            resp = await client.post(
                f"{ODOO_BASE_URL}/api/pos/products",
                json={"page_number": page_number}
            )
            data = resp.json()
            _logger.info("Products fetched from Odoo | Page=%s", page_number)
            return data
    except Exception as e:
        _logger.error("❌ Failed to fetch products from Odoo: %s", e)
        return {"result_type": "error", "detail": str(e)}

@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    """
    Secure WebSocket endpoint for events.
    Expects 'token' as a query parameter.
    """
    if not websocket:
        return

    try:
        token = websocket.query_params["token"]
        _logger.info(f"Received token: {token}")
    except KeyError as e:
        _logger.error(f"Missing token: {e}")
        await websocket.accept()
        await websocket.send_json({
            "type": "connection.authentication",
            "payload": {"result_type": "failure", "detail": "Missing token"}
        })
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 1. Perform background verification
    auth_data = await verify_credentials(token)
    if auth_data.get("result_type") != "success":
        _logger.warning(f"Authentication failed for token: {token}")
        try:
            await websocket.accept()
            await websocket.send_json({"type": "connection.authentication", "payload": {"result_type": "failure"}})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        except Exception:
            _logger.info("Client disconnected before auth failure could be sent")
        return

    # Successful Auth
    await websocket.accept()
    
    # Extract identifiers (User ID and Token)
    result = auth_data.get("result", {})
    user_id = str(result.get("user_id") or result.get("uid") or "")
    
    identifiers = [token]
    if user_id:
        identifiers.append(user_id)
        _logger.info("Authenticated User ID: %s", user_id)

    await manager.connect(identifiers, websocket)

    try:
        while True:
            # 12-hour inactivity timeout
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=43200)
            except asyncio.TimeoutError:
                _logger.info("Closing connection for %s due to 12h inactivity", token)
                break

            event_type = data.get("type")
            _logger.info("📨 Event received | Type=%s", event_type)
            
            if event_type == "payment.callback":
                result_msg = await handle_payment_callback(data)
                await websocket.send_json(result_msg)
            elif event_type == "get.products":
                result_msg = await get_products(data)
                await websocket.send_json(result_msg)
            else:
                _logger.warning("⚠️ Unknown event type: %s", event_type)
                await websocket.send_json({
                    "result_type": "error",
                    "detail": f"Unknown event type: {event_type}"
                })
    except WebSocketDisconnect:
        _logger.info("Client %s disconnected", token)
        manager.disconnect(websocket)
    except Exception as e:
        _logger.error("Exception in websocket for %s: %s", token, e)
        manager.disconnect(websocket)



# NewLeap – Invoice Receiver
@app.post("/api/newleap/pos/order/new")
async def newleap_new_pos_order(payload: PosOrderPayload):
    """
    1. Receives HTTP POST from Odoo (pos.order payment request).
    2. Broadcasts to the authenticated WebSocket client managed by 'manager'.
    """
    _logger.info(
        "📥 POS PAYMENT REQUEST FROM ODOO | UserID=%s | Reference=%s | Amount=%s",
        payload.user_id,
        payload.pos_reference,
        payload.amount,
    )

    # Broadcast to authenticated WebSocket client across all workers via Redis
    try:
        message = {
            "type": "pos.payment.request",
            "payload": {
                "user_id": payload.user_id,
                "newleap_token": NEWLEAP_TOKEN,
                "pos_order": payload.pos_reference,
                "amount": payload.amount,
                "currency": payload.currency,
            }
        }
        # We now send keyed by user_id
        await manager.send_redis_message(payload.user_id, message)
    except Exception as e:
        _logger.error("Failed to send payment request to Redis: %s", e)

    return {"result_type": "success"}
    
