"""Credit / account commands.

/credits : quick Xquik balance check (lightweight call)
/account : full account summary including lifetime stats + monitor billing
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
    app.add_handler(
        CommandHandler(
            "credits",
            lambda u, c: _credits(u, c, settings),
            filters=user_filter,
        )
    )
    app.add_handler(
        CommandHandler(
            "account",
            lambda u, c: _account(u, c, settings),
            filters=user_filter,
        )
    )


async def _credits(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> None:
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


async def _account(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
) -> None:
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

    # Headline: how many /news runs the current balance covers.
    # Each /news run costs ~1 credit per tweet scraped (Xquik bills tweet
    # search at 1 credit per result). We don't know the exact per-run cost
    # without running it, but a rough 1,000-tweet average gives a useful
    # ballpark. Ref: https://docs.xquik.com/guides/billing
    runs_estimate = ""
    try:
        b = float(balance)
        if b > 0:
            # Conservative estimate: ~1,000 tweets per /news run.
            runs_estimate = f" (~{int(b / 1000)} /news runs at ~1k tweets each)"
    except (ValueError, TypeError):
        pass

    # Monitor billing only matters when monitors are actually running.
    burn_hour = mb.get("activeHourlyBurn", "0")
    monitor_section = ""
    try:
        bh = float(burn_hour)
        if bh > 0:
            burn_day = mb.get("activeDailyEstimate", "0")
            per_hour = mb.get("creditsPerActiveMonitorHour", "?")
            # Estimate how long the balance lasts at the current monitor burn.
            runway = ""
            try:
                hours_left = float(balance) / bh
                if hours_left < 48:
                    runway = f" : ~{hours_left:.0f}h of runway left"
                else:
                    runway = f" : ~{hours_left / 24:.1f} days of runway left"
            except (ValueError, TypeError, ZeroDivisionError):
                pass
            monitor_section = (
                f"\n\n*Active monitor burn*\n"
                f"{burn_hour} credits/hr ({per_hour}/hr per monitor)\n"
                f"~{burn_day} credits/day estimate{runway}"
            )
    except (ValueError, TypeError):
        pass

    await update.message.reply_text(
        f"📊 *Xquik account*\n\n"
        f"*Credits*\n"
        f"Balance: *{balance}*{runs_estimate}\n"
        f"Lifetime used: {used} / {purchased} purchased\n\n"
        f"*Cost reference*\n"
        f"Tweet search: 1 credit per tweet returned\n"
        f"(a /news run's cost = number of tweets scraped)"
        f"{monitor_section}",
        parse_mode="Markdown",
    )
