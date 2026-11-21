"""Formatting helpers. Indian digit grouping: 2,30,000."""


def inr(amount: int) -> str:
    sign = "-" if amount < 0 else ""
    s = str(abs(int(amount)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{sign}₹{s}"


def mask(number: str) -> str:
    """Account and ID numbers are only ever shown and spoken as the last four digits."""
    return f"••{number[-4:]}" if number else ""
