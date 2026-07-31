"""Тесты ThermalAnalyzer — тепловизионная классификация снега."""

import pytest

from blueos.thermal_analyzer import (
    HiddenObstacle,
    SnowAssessment,
    ThermalAnalyzer,
    ThermalRegion,
)
from blueos.tilt_controller import SnowType


@pytest.fixture
def analyzer():
    return ThermalAnalyzer()


def uniform_frame(rows, cols, value):
    return [[value for _ in range(cols)] for _ in range(rows)]


# ---------------------------------------------------------------------------
# Initial state / initialize
# ---------------------------------------------------------------------------

def test_initial_not_ready(analyzer):
    assert analyzer.is_ready is False


def test_initial_last_assessment_none(analyzer):
    assert analyzer.last_assessment is None


def test_initialize_sets_ready(analyzer):
    result = analyzer.initialize()
    assert result is True
    assert analyzer.is_ready is True


# ---------------------------------------------------------------------------
# process_frame — no thermal_frame (default stub region)
# ---------------------------------------------------------------------------

def test_process_frame_no_thermal_frame_powder(analyzer):
    assessment = analyzer.process_frame(lidar_depth_mm=50.0, ambient_temp_c=-10.0)
    assert isinstance(assessment, SnowAssessment)
    assert assessment.dominant_type == SnowType.POWDER
    assert assessment.recommended_mode == "HOVER_BLOW"
    assert analyzer.last_assessment is assessment


def test_process_frame_no_thermal_frame_deep_snow_bulldozer(analyzer):
    assessment = analyzer.process_frame(lidar_depth_mm=300.0, ambient_temp_c=-10.0)
    assert assessment.recommended_mode == "BULLDOZER"
    assert assessment.recommended_tilt_deg == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# process_frame — logging every 50 frames
# ---------------------------------------------------------------------------

def test_process_frame_with_real_thermal_frame_stores_map(analyzer):
    frame = uniform_frame(4, 4, -8.0)
    assessment = analyzer.process_frame(thermal_frame=frame, lidar_depth_mm=50.0, ambient_temp_c=-10.0)
    assert analyzer._thermal_map == frame
    assert isinstance(assessment, SnowAssessment)


def test_process_frame_logs_every_50(analyzer):
    for _ in range(50):
        analyzer.process_frame(lidar_depth_mm=10.0, ambient_temp_c=-10.0)
    assert analyzer._frame_count == 50


# ---------------------------------------------------------------------------
# _segment_thermal_map
# ---------------------------------------------------------------------------

def test_segment_no_frame_returns_default_region(analyzer):
    regions = analyzer._segment_thermal_map(None)
    assert len(regions) == 1
    assert regions[0].label == "full_frame"


def test_segment_empty_frame_list_returns_default(analyzer):
    regions = analyzer._segment_thermal_map([])
    assert len(regions) == 1


def test_segment_with_frame_computes_mean_std(analyzer):
    frame = uniform_frame(4, 4, -8.0)
    regions = analyzer._segment_thermal_map(frame)
    assert len(regions) == 1
    assert regions[0].mean_temp_c == pytest.approx(-8.0)
    assert regions[0].std_temp_c == pytest.approx(0.0)


def test_segment_with_frame_no_rows_no_temps():
    analyzer = ThermalAnalyzer()
    # A frame that is truthy (non-empty outer list) but with rows having
    # no elements would create empty all_temps -> function returns []
    regions = analyzer._segment_thermal_map([[]])
    assert regions == []


# ---------------------------------------------------------------------------
# _classify_region
# ---------------------------------------------------------------------------

def test_classify_powder(analyzer):
    region = ThermalRegion(label="r", center_x=0, center_y=0, area_m2=1.0,
                            mean_temp_c=-8.0, std_temp_c=1.0)
    assert analyzer._classify_region(region) == SnowType.POWDER
    assert region.confidence == pytest.approx(0.9)


def test_classify_ice(analyzer):
    region = ThermalRegion(label="r", center_x=0, center_y=0, area_m2=1.0,
                            mean_temp_c=-1.0, std_temp_c=0.1)
    assert analyzer._classify_region(region) == SnowType.ICE
    assert region.confidence == pytest.approx(0.85)


def test_classify_wet(analyzer):
    region = ThermalRegion(label="r", center_x=0, center_y=0, area_m2=1.0,
                            mean_temp_c=-0.5, std_temp_c=1.5)
    assert analyzer._classify_region(region) == SnowType.WET
    assert region.confidence == pytest.approx(0.8)


def test_classify_unknown_too_warm(analyzer):
    region = ThermalRegion(label="r", center_x=0, center_y=0, area_m2=1.0,
                            mean_temp_c=10.0, std_temp_c=1.0)
    assert analyzer._classify_region(region) == SnowType.UNKNOWN
    assert region.confidence == pytest.approx(0.5)


def test_classify_unknown_too_cold_high_std(analyzer):
    region = ThermalRegion(label="r", center_x=0, center_y=0, area_m2=1.0,
                            mean_temp_c=-20.0, std_temp_c=5.0)
    assert analyzer._classify_region(region) == SnowType.UNKNOWN


def test_classify_powder_std_too_high_falls_to_unknown(analyzer):
    # In fresh range but std >= 2.0, and not in wet range → UNKNOWN
    region = ThermalRegion(label="r", center_x=0, center_y=0, area_m2=1.0,
                            mean_temp_c=-8.0, std_temp_c=5.0)
    assert analyzer._classify_region(region) == SnowType.UNKNOWN


# ---------------------------------------------------------------------------
# _detect_hidden_obstacles
# ---------------------------------------------------------------------------

def test_detect_hidden_obstacles_no_frame(analyzer):
    assert analyzer._detect_hidden_obstacles(None, -10.0) == []


def test_detect_hidden_obstacles_zero_cols(analyzer):
    assert analyzer._detect_hidden_obstacles([[]], -10.0) == []


def test_detect_hidden_obstacles_finds_vent_and_pipe(analyzer):
    rows, cols = 8, 8
    frame = uniform_frame(rows, cols, -10.0)
    frame[0][0] = 5.0     # large delta → vent
    frame[4][4] = -3.0    # medium delta → pipe
    obstacles = analyzer._detect_hidden_obstacles(frame, -10.0)
    types = {o.obstacle_type for o in obstacles}
    assert "vent" in types
    assert "pipe" in types


def test_detect_hidden_obstacles_none_found(analyzer):
    frame = uniform_frame(8, 8, -10.0)
    obstacles = analyzer._detect_hidden_obstacles(frame, -10.0)
    assert obstacles == []


# ---------------------------------------------------------------------------
# _get_dominant_type
# ---------------------------------------------------------------------------

def test_dominant_type_empty_regions(analyzer):
    assert analyzer._get_dominant_type([]) == SnowType.UNKNOWN


def test_dominant_type_picks_largest_area(analyzer):
    regions = [
        ThermalRegion(label="a", center_x=0, center_y=0, area_m2=10.0,
                      mean_temp_c=-8, std_temp_c=1, snow_type=SnowType.POWDER),
        ThermalRegion(label="b", center_x=0, center_y=0, area_m2=50.0,
                      mean_temp_c=-1, std_temp_c=1.5, snow_type=SnowType.WET),
    ]
    assert analyzer._get_dominant_type(regions) == SnowType.WET


# ---------------------------------------------------------------------------
# _estimate_density
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snow_type", list(SnowType))
def test_estimate_density_base_values(snow_type):
    density = ThermalAnalyzer._estimate_density(snow_type, ambient_temp=-10.0)
    assert density > 0


def test_estimate_density_warm_increases(analyzer):
    cold = ThermalAnalyzer._estimate_density(SnowType.POWDER, ambient_temp=-10.0)
    warm = ThermalAnalyzer._estimate_density(SnowType.POWDER, ambient_temp=-1.0)
    assert warm > cold


def test_estimate_density_very_cold_decreases(analyzer):
    normal = ThermalAnalyzer._estimate_density(SnowType.POWDER, ambient_temp=-10.0)
    very_cold = ThermalAnalyzer._estimate_density(SnowType.POWDER, ambient_temp=-20.0)
    assert very_cold < normal


# ---------------------------------------------------------------------------
# _estimate_coverage
# ---------------------------------------------------------------------------

def test_coverage_empty_regions(analyzer):
    assert ThermalAnalyzer._estimate_coverage([]) == 0.0


def test_coverage_all_snow(analyzer):
    regions = [
        ThermalRegion(label="a", center_x=0, center_y=0, area_m2=50.0,
                      mean_temp_c=-8, std_temp_c=1, snow_type=SnowType.POWDER),
    ]
    assert ThermalAnalyzer._estimate_coverage(regions) == pytest.approx(100.0)


def test_coverage_partial(analyzer):
    regions = [
        ThermalRegion(label="a", center_x=0, center_y=0, area_m2=50.0,
                      mean_temp_c=-8, std_temp_c=1, snow_type=SnowType.POWDER),
        ThermalRegion(label="b", center_x=0, center_y=0, area_m2=50.0,
                      mean_temp_c=10, std_temp_c=1, snow_type=SnowType.UNKNOWN),
    ]
    assert ThermalAnalyzer._estimate_coverage(regions) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# _recommend_strategy
# ---------------------------------------------------------------------------

def test_strategy_powder_shallow():
    mode, tilt = ThermalAnalyzer._recommend_strategy(SnowType.POWDER, 100.0, 100.0)
    assert mode == "HOVER_BLOW"
    assert tilt == 0.0


def test_strategy_powder_deep():
    mode, tilt = ThermalAnalyzer._recommend_strategy(SnowType.POWDER, 300.0, 100.0)
    assert mode == "BULLDOZER"
    assert tilt == pytest.approx(15.0)


def test_strategy_settled():
    mode, tilt = ThermalAnalyzer._recommend_strategy(SnowType.SETTLED, 200.0, 350.0)
    assert mode == "BULLDOZER"
    assert tilt == pytest.approx(23.0)


def test_strategy_wet_shallow():
    mode, tilt = ThermalAnalyzer._recommend_strategy(SnowType.WET, 100.0, 500.0)
    assert mode == "BULLDOZER"
    assert tilt == pytest.approx(28.0)


def test_strategy_wet_too_deep_skip():
    mode, tilt = ThermalAnalyzer._recommend_strategy(SnowType.WET, 300.0, 500.0)
    assert mode == "SKIP"
    assert tilt == 0.0


def test_strategy_ice_skip():
    mode, tilt = ThermalAnalyzer._recommend_strategy(SnowType.ICE, 50.0, 800.0)
    assert mode == "SKIP"


def test_strategy_unknown_default():
    mode, tilt = ThermalAnalyzer._recommend_strategy(SnowType.UNKNOWN, 100.0, 300.0)
    assert mode == "BULLDOZER"
    assert tilt == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# HiddenObstacle dataclass default
# ---------------------------------------------------------------------------

def test_hidden_obstacle_default_confidence():
    obs = HiddenObstacle(x=1.0, y=2.0, radius=0.5, obstacle_type="pipe", delta_temp_c=6.0)
    assert obs.confidence == 0.0
