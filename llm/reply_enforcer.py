def enforce_reply(parsed):

    reply = parsed.get("reply", "")

    if "Regards" in reply:
        reply = reply.replace("Regards", "")

    return reply.strip()