"""Credit / account commands.

/credits — quick Xquik balance check (lightweight call)
/account — full account summary including lifetime stats + monitor billing
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import Settings
from xquik import XquikClient, XquikError

log = logging.getLogger("agent.credits")


def register(app: Application, settings: Settings, user_filter) -> None:
    app.add_handler(CommandHandler("credits", _credits, filters=user_filter))
    app.add_handler(CommandHandler("account", _account, filters=user_filter))


async def _credits(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    client = XquikClient(api_key=settings.xquik_api_key)
    try:
        data = await asyncio.to_thread(client.get_credits)
    except XquikError as e:
        await update.message.reply_text(f"Couldn't fetch credits: {e}")
        return

    balance = data.get("balance", "?")
    used = data.get("lifetime_used", "?")
    purchased = data.get("lifetime_purchased", "?")
    auto = data.get("auto_topup_enabled", False)

    # When auto-topup is on, show the threshold/amount too.
    auto_line = ""
    if auto:
        thresh = data.get("auto_topup_threshold", "?")
        amt = data.get("auto_topup_amount_dollars", "?")
        auto_line = f"\n♻️ Auto-topup: +{amt} credits under {thresh}"

    await update.message.reply_text(
        f"💰 *Xquik credits*\n\n"
        f"Balance: *{balance}*\n"
        f"Lifetime used: {used}\n"
        f"Lifetime purchased: {purchased}"
        f"{auto_line}",
        parse_mode="Markdown",
    )


async def _account(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = ctx.application.bot_data["settings"]
    client = XquikClient(api_key=settings.xquik_api_key)
    try:
        data = await asyncio.to_thread(client.get_account)
    except XquikError as e:
        await update.message.reply_text(f"Couldn't fetch account: {e}")
        return

    ci = data.get("creditInfo", {}) or {}
    mb = data.get("monitorBilling", {}) or {}

    balance = ci.get("balance", "?")
    used = ci.get("lifetimeUsed", "?")
    purchased = ci.get("lifetimePurchased", "?")

    burn_hour = mb.get("activeHourlyBurn", "0")
    burn_day = mb.get("activeDailyEstimate", "0")
    per_hour = mb.get("creditsPerActiveMonitorHour", "?")
    per_day = mb.get("creditsPerActiveMonitorDay", "?")

    # If there's an active burn rate, estimate how long the balance lasts.
    runway = ""
    try:
        b = float(balance)
        h = float(burn_hour)
        if h > 0 and b > 0:
            hours_left = b / h
            if hours_left < 48:
                runway = f"\n⏳ At current burn: ~{hours_left:.0f}h left"
            else:
                runway = f"\n⏳ At current burn: ~{hours_left / 24:.1f} days left"
    except (ValueError, TypeError, ZeroDivisionError):
        pass

    await update.message.reply_text(
        f"📊 *Xquik account*\n\n"
        f"*Credits*\n"
        f"Balance: {balance}\n"
        f"Lifetime used: {used} / {purchased} purchased\n\n"
        f"*Monitor billing*\n"
        f"Hourly burn: {burn_hour} ({per_hour}/hr per monitor)\n"
        f"Daily estimate: {burn_day} ({per_day}/day per monitor)"
        f"{runway}",
        parse_mode="Markdown",
    )
