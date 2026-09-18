from fastapi import APIRouter
from pydantic import BaseModel
from decimal import Decimal
from typing import Optional
from app import db
from app.games import crash, mines, plinko, tower, slots, roulette, blackjack, dice, upgrader

router = APIRouter(tags=["games"])


class BetReq(BaseModel):
    tg_id: int
    game: str
    bet: float
    params: Optional[dict] = None


@router.post("/bet")
async def bet(req: BetReq):
    user = await db.get_or_create_user(req.tg_id)
    bet_amount = Decimal(str(req.bet))
    params = req.params or {}
    step = params.get("step", 0)  # step 0 = first call (deduct), step>0 = continuation

    if bet_amount <= 0:
        return {"error": "invalid_bet"}

    # Only deduct balance on step 0 (first call of a game)
    if step == 0:
        if user["balance"] < bet_amount:
            return {"error": "insufficient_balance"}
        u = await db.update_balance(user["id"], -bet_amount)
        await db.record_tx(user["id"], "bet", req.game, -bet_amount, u["balance"])
    else:
        u = dict(user)

    game = req.game

    try:
        if game == "crash":
            result = crash.play(bet_amount, params)
        elif game == "mines":
            result = mines.play(bet_amount, params)
        elif game == "plinko":
            result = plinko.play(bet_amount, params)
        elif game == "tower":
            result = tower.play(bet_amount, params)
        elif game == "slots":
            result = slots.play(bet_amount, params)
        elif game == "roulette":
            result = roulette.play(bet_amount, params)
        elif game == "blackjack":
            result = blackjack.play(bet_amount, params)
        elif game == "dice":
            result = dice.play(bet_amount, params)
        elif game == "upgrader":
            result = upgrader.play(bet_amount, params)
        else:
            return {"error": "unknown_game"}
    except Exception as e:
        return {"error": str(e)}

    win_amount = Decimal(str(result.get("win", 0)))

    if win_amount > 0:
        u2 = await db.update_balance(user["id"], win_amount)
        await db.record_tx(user["id"], "win", req.game, win_amount, u2["balance"])
        # Update stats
        profit = float(win_amount - bet_amount)
        if profit > 0:
            await db._db.execute(
                "UPDATE users SET total_won=total_won+? WHERE id=?", (profit, user["id"])
            )
            await db._db.commit()
        result["balance"] = float(u2["balance"])
    else:
        if step == 0:
            await db._db.execute(
                "UPDATE users SET total_lost=total_lost+? WHERE id=?", (float(bet_amount), user["id"])
            )
            await db._db.commit()
        result["balance"] = float(u["balance"])

    return result
