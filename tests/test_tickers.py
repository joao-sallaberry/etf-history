from etf_history.tickers import br_ticker


def test_br_ticker():
    assert br_ticker("BRPETRACNPR6") == "PETR4"
    assert br_ticker("BRPETRACNOR9") == "PETR3"
    assert br_ticker("BRSANBCDAM13") == "SANB11"
    assert br_ticker("BRBRSRACNPB4") == "BRSR6"
    assert br_ticker("BRABCBD19PR1") == ""  # subscription rights
    assert br_ticker("US0594603039") == ""
