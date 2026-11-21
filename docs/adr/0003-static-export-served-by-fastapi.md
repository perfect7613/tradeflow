# 3. Static PWA served by FastAPI from one origin

Status: accepted

The Next.js app is a static export (`web/out`) served by the FastAPI backend. One origin means one
HTTPS tunnel covers the PWA, REST, Server-Sent Events, the voice WebSockets and Cashfree webhooks,
and the iPhone gets the secure context it needs for the microphone. `next dev` still works against
`NEXT_PUBLIC_API=http://localhost:8000`.
