"""Tests for EufyLife authentication manager."""

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from custom_components.eufylife_api.auth import EufyLifeAuthManager, TOKEN_REFRESH_BUFFER


@pytest.fixture
def mock_session():
    """Create a mock aiohttp session."""
    return Mock(spec=aiohttp.ClientSession)


@pytest.fixture
def auth_manager(mock_session):
    """Create an EufyLifeAuthManager instance."""
    return EufyLifeAuthManager(
        session=mock_session,
        email="test@example.com",
        password="test_password"
    )


@pytest.fixture
def successful_auth_response():
    """Mock successful authentication response."""
    return {
        "res_code": 1,
        "access_token": "new_test_token_12345",
        "user_id": "test_user_id",
        "expires_in": 2592000,  # 30 days
        "devices": [{"id": "device_123"}],
        "customers": [{"id": "customer_456"}, {"id": "customer_789"}]
    }


class TestShouldRefreshToken:
    """Tests for should_refresh_token method."""

    def test_should_refresh_when_expired(self, auth_manager):
        """Test that token refresh is needed when token has expired."""
        # Token expired 1 hour ago
        expires_at = time.time() - 3600
        assert auth_manager.should_refresh_token(expires_at) is True

    def test_should_refresh_when_near_expiry(self, auth_manager):
        """Test that token refresh is needed 1 hour before expiration."""
        # Token expires in 30 minutes (within 1-hour buffer)
        expires_at = time.time() + 1800
        assert auth_manager.should_refresh_token(expires_at) is True

    def test_should_not_refresh_when_valid(self, auth_manager):
        """Test that token refresh is not needed when token is still valid."""
        # Token expires in 2 hours (outside 1-hour buffer)
        expires_at = time.time() + 7200
        assert auth_manager.should_refresh_token(expires_at) is False

    def test_should_refresh_exactly_at_buffer(self, auth_manager):
        """Test behavior exactly at the refresh buffer threshold."""
        # Token expires exactly in 1 hour
        expires_at = time.time() + TOKEN_REFRESH_BUFFER
        assert auth_manager.should_refresh_token(expires_at) is True


class TestAuthenticate:
    """Tests for authenticate method."""

    @pytest.mark.asyncio
    async def test_successful_authentication(self, auth_manager, mock_session, successful_auth_response):
        """Test successful authentication with EufyLife API."""
        # Mock successful API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=successful_auth_response)

        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await auth_manager.authenticate()

        assert result is not None
        assert result["access_token"] == "new_test_token_12345"
        assert result["user_id"] == "test_user_id"
        assert "expires_at" in result
        assert result["device_id"] == "device_123"
        assert result["customer_ids"] == ["customer_456", "customer_789"]

    @pytest.mark.asyncio
    async def test_failed_authentication_invalid_credentials(self, auth_manager, mock_session):
        """Test failed authentication with invalid credentials."""
        # Mock failed API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "res_code": 0,
            "message": "Invalid credentials"
        })

        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await auth_manager.authenticate()

        assert result is None

    @pytest.mark.asyncio
    async def test_failed_authentication_http_error(self, auth_manager, mock_session):
        """Test failed authentication due to HTTP error."""
        # Mock HTTP error response
        mock_response = AsyncMock()
        mock_response.status = 500

        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await auth_manager.authenticate()

        assert result is None

    @pytest.mark.asyncio
    async def test_authentication_timeout(self, auth_manager, mock_session):
        """Test authentication timeout handling."""
        # Mock timeout
        mock_session.post = AsyncMock(side_effect=asyncio.TimeoutError())

        result = await auth_manager.authenticate()

        assert result is None


class TestRefreshWithRetry:
    """Tests for refresh_with_retry method."""

    @pytest.mark.asyncio
    async def test_successful_refresh_first_attempt(self, auth_manager, successful_auth_response):
        """Test successful token refresh on first attempt."""
        with patch.object(auth_manager, 'authenticate', AsyncMock(return_value=successful_auth_response)):
            result = await auth_manager.refresh_with_retry()

            assert result is not None
            assert result == successful_auth_response
            # Should only call authenticate once
            assert auth_manager.authenticate.call_count == 1

    @pytest.mark.asyncio
    async def test_successful_refresh_second_attempt(self, auth_manager, successful_auth_response):
        """Test successful token refresh on second attempt after one failure."""
        # First call fails, second succeeds
        with patch.object(
            auth_manager,
            'authenticate',
            AsyncMock(side_effect=[None, successful_auth_response])
        ):
            with patch('asyncio.sleep', AsyncMock()):
                result = await auth_manager.refresh_with_retry()

                assert result is not None
                assert result == successful_auth_response
                # Should call authenticate twice
                assert auth_manager.authenticate.call_count == 2

    @pytest.mark.asyncio
    async def test_successful_refresh_third_attempt(self, auth_manager, successful_auth_response):
        """Test successful token refresh on third attempt after two failures."""
        # First two calls fail, third succeeds
        with patch.object(
            auth_manager,
            'authenticate',
            AsyncMock(side_effect=[None, None, successful_auth_response])
        ):
            with patch('asyncio.sleep', AsyncMock()):
                result = await auth_manager.refresh_with_retry()

                assert result is not None
                assert result == successful_auth_response
                # Should call authenticate three times
                assert auth_manager.authenticate.call_count == 3

    @pytest.mark.asyncio
    async def test_failed_refresh_all_attempts(self, auth_manager):
        """Test token refresh failure after all 3 retry attempts."""
        # All attempts fail
        with patch.object(auth_manager, 'authenticate', AsyncMock(return_value=None)):
            with patch('asyncio.sleep', AsyncMock()):
                result = await auth_manager.refresh_with_retry()

                assert result is None
                # Should call authenticate 4 times (initial + 3 retries)
                assert auth_manager.authenticate.call_count == 4

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self, auth_manager):
        """Test that exponential backoff uses correct delays."""
        sleep_calls = []

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        # All attempts fail to test all delays
        with patch.object(auth_manager, 'authenticate', AsyncMock(return_value=None)):
            with patch('asyncio.sleep', mock_sleep):
                await auth_manager.refresh_with_retry()

                # Should have delays: 1s, 5s, 15s (no delay on first attempt)
                assert sleep_calls == [1, 5, 15]


class TestIntegrationScenarios:
    """Integration tests for common authentication scenarios."""

    @pytest.mark.asyncio
    async def test_token_refresh_before_expiry(self, auth_manager, successful_auth_response):
        """Test complete flow: detect expiring token and refresh."""
        # Token expires in 30 minutes (within 1-hour buffer)
        expires_at = time.time() + 1800

        # Should detect need for refresh
        assert auth_manager.should_refresh_token(expires_at) is True

        # Successful refresh
        with patch.object(auth_manager, 'authenticate', AsyncMock(return_value=successful_auth_response)):
            result = await auth_manager.refresh_with_retry()

            assert result is not None
            assert "access_token" in result
            assert "expires_at" in result

    @pytest.mark.asyncio
    async def test_no_refresh_when_token_valid(self, auth_manager):
        """Test that no refresh attempt is made when token is still valid."""
        # Token expires in 2 hours (outside 1-hour buffer)
        expires_at = time.time() + 7200

        # Should not detect need for refresh
        assert auth_manager.should_refresh_token(expires_at) is False

        # No authenticate call should be made if checked before attempting refresh
        with patch.object(auth_manager, 'authenticate', AsyncMock()) as mock_auth:
            # Simulate proper coordinator logic: only refresh if needed
            if auth_manager.should_refresh_token(expires_at):
                await auth_manager.refresh_with_retry()

            # authenticate should not have been called
            mock_auth.assert_not_called()
