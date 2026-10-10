import pytest
from unittest.mock import MagicMock, patch
from sdr_collector import SDRCollector


def test_sdr_standby_and_warmup():
    """SDR のスタンバイ（省電力クローズ）とウォームアップ（再オープン）の動作検証"""
    collector = SDRCollector(mock_sdr=True)
    collector.start()
    assert collector._is_running is True

    # スタンバイ移行（SDRクローズ）
    collector.standby()
    assert collector.is_standby is True
    assert collector._is_running is False

    # ウォームアップ（SDRオープン & チューニング）
    collector.warmup(center_freq_hz=437550000.0)
    assert collector.is_standby is False
    assert collector._is_running is True


def test_sdr_hardware_close_on_standby():
    """実機 RTL-SDR ハンドルが standby で適切に close されることの検証"""
    collector = SDRCollector(mock_sdr=False)
    mock_rtlsdr_inst = MagicMock()

    with patch("sdr_collector.RtlSdr", return_value=mock_rtlsdr_inst), \
         patch("sdr_collector.HAS_RTLSDR", True):
        collector.start()
        assert collector.sdr is mock_rtlsdr_inst

        collector.standby()
        mock_rtlsdr_inst.close.assert_called_once()
        assert collector.sdr is None
        assert collector.is_standby is True

        # 再度ウォームアップ
        collector.warmup(center_freq_hz=437550000.0)
        assert collector.sdr is not None
        assert collector.is_standby is False
