"""Multipart spool ownership on cancellation/errors, entirely in memory.

Strong references and close-call counters make these tests independent of
reference counting or garbage collection. No real temporary files are created.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from unittest.mock import patch

try:
    from python_multipart.exceptions import MultipartParseError
    from starlette.datastructures import Headers
    from starlette.exceptions import HTTPException
    from starlette.formparsers import MultiPartException
    from starlette.requests import ClientDisconnect

    from themeshift.web import app as web_app
except ImportError:
    WEB_AVAILABLE = False
else:
    WEB_AVAILABLE = True


class MemorySpool(io.BytesIO):
    """Match the in-memory portion of SpooledTemporaryFile for UploadFile."""

    _rolled = False
    _max_size = 1024 * 1024

    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


@unittest.skipUnless(WEB_AVAILABLE, "Install .[web] to run multipart tests")
class MultipartCleanupTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.opened: list[MemorySpool] = []
        self.patcher = patch("starlette.formparsers.SpooledTemporaryFile", self.make_spool)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.close_remaining)

    def make_spool(self, *args, **kwargs) -> MemorySpool:
        file = MemorySpool()
        self.opened.append(file)
        return file

    def close_remaining(self) -> None:
        for file in self.opened:
            if not file.closed:
                file.close()

    def parser(self, chunks):
        return web_app.ClosingMultiPartParser(
            Headers({"content-type": "multipart/form-data; boundary=boundary"}),
            chunks,
            max_files=2,
            max_fields=2,
            max_part_size=16 * 1024,
        )

    @staticmethod
    def partial_two_file_body() -> bytes:
        # The first file has completed; the second is still in progress. Both
        # are owned by the parser until a FormData object is successfully handed
        # to the caller, so both must close if receiving the next chunk fails.
        return (
            b'--boundary\r\nContent-Disposition: form-data; name="one"; filename="one.mtz"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\nfirst file\r\n"
            b'--boundary\r\nContent-Disposition: form-data; name="two"; filename="two.mtz"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\nsecond partial file"
        )

    def assert_all_closed_immediately(self) -> None:
        self.assertEqual(len(self.opened), 2)
        for file in self.opened:
            self.assertTrue(file.closed)
            self.assertGreaterEqual(file.close_calls, 1, "must explicitly close, not wait for GC")

    async def assert_failure_closes_spools(self, error: BaseException) -> None:
        async def chunks():
            yield self.partial_two_file_body()
            raise error

        parser = self.parser(chunks())
        with self.assertRaises(type(error)) as caught:
            await parser.parse()
        self.assertIs(caught.exception, error, "the original failure must still reach the HTTP layer")
        self.assert_all_closed_immediately()

    async def test_streaming_body_limit_http_413_closes_all_spools(self) -> None:
        await self.assert_failure_closes_spools(HTTPException(413, "body budget exceeded"))

    async def test_client_disconnect_closes_all_spools(self) -> None:
        await self.assert_failure_closes_spools(ClientDisconnect())

    async def test_cancelled_error_closes_all_spools(self) -> None:
        await self.assert_failure_closes_spools(asyncio.CancelledError())

    async def test_unexpected_stream_error_also_closes_all_spools(self) -> None:
        await self.assert_failure_closes_spools(RuntimeError("stream failed"))

    async def test_wait_for_timeout_closes_files_before_raising_timeout(self) -> None:
        waiting_for_more = asyncio.Event()
        never_finishes = asyncio.Event()

        async def chunks():
            yield self.partial_two_file_body()
            # Reaching this line proves the parser has processed the prefix and
            # opened both files, avoiding a race with an extremely short timer.
            waiting_for_more.set()
            await never_finishes.wait()

        task = asyncio.create_task(self.parser(chunks()).parse())
        try:
            await asyncio.wait_for(waiting_for_more.wait(), timeout=2)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=0.01)
            self.assert_all_closed_immediately()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_truncated_form_without_final_boundary_closes_all_spools(self) -> None:
        async def chunks():
            yield self.partial_two_file_body()

        with self.assertRaises((MultiPartException, MultipartParseError)):
            await self.parser(chunks()).parse()
        self.assert_all_closed_immediately()

    async def assert_bad_second_header_closes_first_file(self, headers: bytes) -> None:
        async def chunks():
            yield (
                b'--boundary\r\nContent-Disposition: form-data; name="one"; filename="one.mtz"\r\n'
                b"\r\nfirst file\r\n--boundary\r\n"
                b'Content-Disposition: form-data; name="two"; filename="two.mtz"\r\n'
                + headers + b"\r\nsecond file\r\n--boundary--\r\n"
            )

        with self.assertRaises((MultiPartException, MultipartParseError)):
            await self.parser(chunks()).parse()
        self.assertEqual(len(self.opened), 1, "second file must not open before its headers validate")
        self.assertTrue(self.opened[0].closed)
        self.assertGreaterEqual(self.opened[0].close_calls, 1)

    async def test_oversized_part_header_closes_previously_opened_spool(self) -> None:
        await self.assert_bad_second_header_closes_first_file(b"X-Large: " + b"x" * (16 * 1024) + b"\r\n")

    async def test_excessive_part_header_count_closes_previously_opened_spool(self) -> None:
        headers = b"".join(f"X-Header-{index}: value\r\n".encode() for index in range(32))
        await self.assert_bad_second_header_closes_first_file(headers)

    async def test_success_keeps_files_open_until_the_caller_closes_form(self) -> None:
        async def chunks():
            yield self.partial_two_file_body()
            yield b"\r\n--boundary--\r\n"

        form = await self.parser(chunks()).parse()
        try:
            self.assertEqual(len(self.opened), 2)
            self.assertTrue(all(not file.closed and file.close_calls == 0 for file in self.opened))
            self.assertEqual(await form["one"].read(), b"first file")
            self.assertEqual(await form["two"].read(), b"second partial file")
        finally:
            await form.close()
        self.assert_all_closed_immediately()


if __name__ == "__main__":
    unittest.main()
