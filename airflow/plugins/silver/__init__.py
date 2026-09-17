"""Silver transforms. One module per source; each exposes build(conn) -> stats.

Facts are full-refresh (TRUNCATE + INSERT in one transaction) from all of
Bronze, so a re-run over the same Bronze is byte-for-byte deterministic.
dim_player_master and vendor_player_map are persistent: player_sk never
changes and a vendor id is resolved once.
"""
