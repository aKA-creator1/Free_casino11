import json
from fastapi import APIRouter
from pydantic import BaseModel
from decimal import Decimal
from app import db

router = APIRouter(tags=["pvp"])


@router.get("/rooms")
async def list_rooms():
    cur = await db._db.execute(
        "SELECT r.*, u1.username as p1_name, u2.username as p2_name "
        "FROM pvp_rooms r "
        "LEFT JOIN users u1 ON r.player1_id=u1.id "
        "LEFT JOIN users u2 ON r.player2_id=u2.id "
        "WHERE r.status IN ('waiting','playing') "
        "ORDER BY r.created_at DESC LIMIT 20"
    )
    return [dict(r) for r in await cur.fetchall()]


class JoinRoomReq(BaseModel):
    tg_id: int
    room_id: int


@router.post("/join")
async def join_room(req: JoinRoomReq):
    user = await db.get_or_create_user(req.tg_id)
    cur = await db._db.execute("SELECT * FROM pvp_rooms WHERE id=?", (req.room_id,))
    room = await cur.fetchone()
    if not room:
        return {"error": "room_not_found"}
    room = dict(room)
    if room["status"] != "waiting":
        return {"error": "room_not_available"}
    if room["player1_id"] == user["id"]:
        return {"error": "cant_join_own"}
    bet = Decimal(str(room["bet"]))
    if user["balance"] < bet:
        return {"error": "insufficient_balance"}
    await db.update_balance(user["id"], -bet)
    await db._db.execute(
        "UPDATE pvp_rooms SET player2_id=?, status='playing' WHERE id=?",
        (user["id"], req.room_id)
    )
    await db._db.commit()
    return {"status": "joined", "room_id": req.room_id}


class CreateRoomReq(BaseModel):
    tg_id: int
    game_type: str
    bet: float


@router.post("/create")
async def create_room(req: CreateRoomReq):
    user = await db.get_or_create_user(req.tg_id)
    bet = Decimal(str(req.bet))
    if user["balance"] < bet:
        return {"error": "insufficient_balance"}
    if req.game_type not in ("rps", "blackjack21", "duel"):
        return {"error": "invalid_game_type"}
    await db.update_balance(user["id"], -bet)
    cur = await db._db.execute(
        "INSERT INTO pvp_rooms(game_type,player1_id,bet,status) VALUES(?,?,?,?)",
        (req.game_type, user["id"], float(bet), "waiting")
    )
    await db._db.commit()
    return {"room_id": cur.lastrowid, "status": "waiting"}


class PVPActionReq(BaseModel):
    tg_id: int
    room_id: int
    action: str


@router.post("/action")
async def pvp_action(req: PVPActionReq):
    user = await db.get_or_create_user(req.tg_id)
    cur = await db._db.execute("SELECT * FROM pvp_rooms WHERE id=?", (req.room_id,))
    room = await cur.fetchone()
    if not room or room["status"] != "playing":
        return {"error": "invalid_room"}
    room = dict(room)
    if room["player1_id"] != user["id"] and room["player2_id"] != user["id"]:
        return {"error": "not_in_room"}

    state = json.loads(room["state"]) if room["state"] else {}

    if room["game_type"] == "rps":
        import random
        player_num = 1 if room["player1_id"] == user["id"] else 2
        state[f"p{player_num}_choice"] = req.action

        if "p1_choice" in state and "p2_choice" in state:
            c1, c2 = state["p1_choice"], state["p2_choice"]
            if c1 == c2:
                result = "draw"
            elif (c1 == "rock" and c2 == "scissors") or (c1 == "scissors" and c2 == "paper") or (c1 == "paper" and c2 == "rock"):
                result = "player1"
            else:
                result = "player2"
            state["result"] = result
            await db._db.execute(
                "UPDATE pvp_rooms SET state=?, status='finished', result=? WHERE id=?",
                (json.dumps(state), result, req.room_id)
            )
            await db._db.commit()
            bet = Decimal(str(room["bet"]))
            if result == "draw":
                await db.update_balance(room["player1_id"], bet)
                await db.update_balance(room["player2_id"], bet)
            elif result == "player1":
                await db.update_balance(room["player1_id"], bet * 2)
            else:
                await db.update_balance(room["player2_id"], bet * 2)
            return {"state": state, "result": result, "finished": True}
        else:
            await db._db.execute("UPDATE pvp_rooms SET state=? WHERE id=?", (json.dumps(state), req.room_id))
            await db._db.commit()
            return {"state": state, "finished": False}

    return {"state": state, "finished": False}
