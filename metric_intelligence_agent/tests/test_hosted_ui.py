import unittest

from streamlit.testing.v1 import AppTest


def hosted_query_test_app(failure_kind=None, api_key_ready=True):
    import httpx
    import streamlit as st
    from openai import AuthenticationError

    import app_pages.query as query_page

    st.session_state.setdefault("data_source", "demo")
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("query_cache", {})
    st.session_state.setdefault("graph", object())
    st.session_state.setdefault("eval_conn", object())
    st.session_state.setdefault("rating_submitted", False)

    def executor(*args, **kwargs):
        if failure_kind == "authentication":
            request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            response = httpx.Response(401, request=request)
            raise AuthenticationError(
                "invalid key at C:\\Users\\private",
                response=response,
                body=None,
            )
        if failure_kind == "unexpected":
            raise RuntimeError("private failure at C:\\Users\\private")
        raise AssertionError("The missing-key path must not execute the runner")

    query_page.run_question = executor
    query_page.show(api_key_ready=api_key_ready, hosted_mode=True)


class HostedUiTests(unittest.TestCase):
    def _app(self, failure_kind=None, api_key_ready=True):
        return AppTest.from_function(
            hosted_query_test_app,
            args=(failure_kind, api_key_ready),
            default_timeout=10,
        ).run()

    def test_missing_key_disables_query_input(self):
        app = self._app(api_key_ready=False)

        self.assertFalse(app.exception)
        self.assertTrue(app.chat_input[0].disabled)
        self.assertIn("Enter your OpenAI API key", app.warning[0].value)

    def test_authentication_error_is_friendly_and_redacted(self):
        app = self._app(failure_kind="authentication")
        app.chat_input[0].set_value("What is total revenue?").run()

        self.assertFalse(app.exception)
        rendered = " ".join(error.value for error in app.error)
        self.assertIn("OpenAI rejected this API key", rendered)
        self.assertNotIn("C:\\Users", rendered)
        self.assertNotIn("invalid key", rendered)

    def test_unexpected_error_is_friendly_and_redacted(self):
        app = self._app(failure_kind="unexpected")
        app.chat_input[0].set_value("What is total revenue?").run()

        self.assertFalse(app.exception)
        rendered = " ".join(error.value for error in app.error)
        self.assertIn("could not complete that request", rendered)
        self.assertNotIn("C:\\Users", rendered)
        self.assertNotIn("private failure", rendered)


if __name__ == "__main__":
    unittest.main()
