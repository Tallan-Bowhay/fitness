# ==========================================
# imports
# ==========================================
from datetime import date
import plotly as plt
import polars as pl
import pandas as pd
import plotly.express as px

from faicons import icon_svg
import gspread

from shiny import render, reactive
from shiny.express import input, ui
from shiny import ui as core_ui
from shiny.express.ui import tooltip
from shiny.ui import page_navbar
from shinywidgets import render_widget

from datetime import date, timedelta

def generate_date_range(dates):
    if not dates:
        return []

    start = date.fromisoformat(dates[0])
    end = date.fromisoformat(dates[-1])

    result = []
    current = start

    while current <= end:
        result.append(current.isoformat())
        current += timedelta(days=1)

    return result

# ==========================================
# Load Data
# ==========================================

google_creds = gspread.service_account(
    filename= # Hidden
)

sheet_ids = [""] # Hidden

def read_in_google_sheet_to_pandas(
    sheet_id: str,
    sheet_name: str,
    gc: gspread.Client,
    header_row: bool = True,
) -> pd.DataFrame:
    """
    Read a worksheet from a Google Sheet into a pandas DataFrame.
    Requires Google Sheet credentials and sheet Id
    """
    worksheet = gc.open_by_key(sheet_id).worksheet(sheet_name)

    values = worksheet.get_all_values()

    if not values:
        return pd.DataFrame()

    if header_row:
        return pd.DataFrame(values[1:], columns=values[0])

    return pd.DataFrame(values)

def read_in_google_sheet_to_polars(
    sheet_id: str,
    sheet_name: str,
    gc: gspread.Client,
    header_row: bool = True,
) -> pl.DataFrame:
    """
    Read a worksheet from a Google Sheet into a Polars DataFrame.
    Requires Google Sheet credentials and sheet ID.
    """
    worksheet = gc.open_by_key(sheet_id).worksheet(sheet_name)

    values = worksheet.get_all_values()

    if not values:
        return pl.DataFrame()

    if header_row:
        return pl.DataFrame(
            values[1:],
            schema=values[0],
            orient="row",
        )

    return pl.DataFrame(values, orient="row")


running_dfs = []
strength_training_dfs = []

for sheet_id in sheet_ids:
    running_dfs.append(
        read_in_google_sheet_to_polars(
            sheet_id=sheet_id,
            sheet_name="Running Log",
            gc=google_creds,
        )
    )
    strength_training_dfs.append(
        read_in_google_sheet_to_polars(
            sheet_id=sheet_id,
            sheet_name="Workout Log",
            gc=google_creds,
        )
    )

running_df = pl.concat(running_dfs) if running_dfs else pl.DataFrame()
strength_training_df = pl.concat(strength_training_dfs) if strength_training_dfs else pl.DataFrame()

# ==========================================
# Constants
# ==========================================
running_distance_units = ["miles", "kilometers"]

current_year = date.today().year
years = [str(y) for y in range(2026, current_year + 1)] + ["All Data"]
months = ["YTD",
          "Jan", "Feb", "Mar",
          "Apr", "May", "Jun",
          "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec"]

body_part_colors_group_order = {
    #Upper
    "Arms" : "#e99fe0",
    "Chest" : "#cc7577",
    "Shoulders" : "#8c0203",
    #Trunk
    "Back - Lats" : "#b67d12",
    "Back - Other" : "#f68b03",
    "Core" : "#556503",
    #Legs
    "Legs - Glutes/Hams" : "#6ba0ac",
    "Legs - Quads" : "#498dc1",
    "Legs - Other" : "#7b5297",
    # Other
    "Miscellaneous" : "#e8eaed"
}

body_part_colors_alp_order = {
    "Arms" : "#e99fe0",
    "Back - Lats" : "#b67d12",
    "Back - Other" : "#f68b03",
    "Core" : "#556503",
    "Chest" : "#cc7577",
    "Legs - Glutes/Hams" : "#6ba0ac",
    "Legs - Quads" : "#498dc1",
    "Legs - Other" : "#7b5297",
    "Shoulders" : "#8c0203",
    "Miscellaneous" : "#e8eaed"
}

ui.page_opts(title="My Dashboard", fillable=True)

# ==========================================
# Data Manipulation
# ==========================================
running_df = running_df.rename({c: c.strip() for c in running_df.columns})

running_df = (
    running_df
    .select(["Date", "Miles Ran", "Km Ran", "First Mile Time", "Total Time", "Run Type", "Notes"])
    .with_columns(pl.all().str.strip_chars())  # strip whitespace in values
    .with_columns(
        pl.col("Date").str.replace(",", "").str.to_date("%B %d %Y", strict=False),
        pl.col("Miles Ran", "Km Ran", "First Mile Time", "Total Time").cast(pl.Float64, strict=False),
    )
    .with_columns(pl.col("Miles Ran", "Km Ran").fill_null(0))
)

strength_training_df = strength_training_df.rename({c: c.strip() for c in strength_training_df.columns})

strength_training_df = (
    strength_training_df
    .select(["Date", "Main Body Part", "Exercise", "Weight",
             "Reps / Time (Sec.)", "Set #", "Notes"])
    .with_columns(pl.col(pl.String).str.strip_chars())  # only touches string columns
    .with_columns(
        # blank strings -> null
        pl.when(pl.col("Main Body Part", "Exercise", "Notes") == "")
        .then(None)
        .otherwise(pl.col("Main Body Part", "Exercise", "Notes"))
        .name.keep()
    )
    .with_columns(
        pl.col("Date").str.to_date("%m/%d/%Y", strict=False),
        pl.col("Weight").cast(pl.Float64, strict=False),
        pl.col("Reps / Time (Sec.)", "Set #").cast(pl.Int64, strict=False),
    )
    .with_row_index("row_id")
)

# Fill missing set numbers: per date / body part / exercise, use the lowest unused number from 1 up
def fill_sets(g: pl.DataFrame) -> pl.DataFrame:
    sets = g["Set #"].to_list()
    used = {s for s in sets if s is not None}
    n, filled = 1, []
    for s in sets:
        if s is None:
            while n in used:
                n += 1
            used.add(n)
            s = n
        filled.append(s)
    return g.with_columns(pl.Series("Set #", filled, dtype=pl.Int64))

strength_training_df = (
    pl.concat([
        fill_sets(g)
        for _, g in strength_training_df.group_by(
            ["Date", "Main Body Part", "Exercise"], maintain_order=True
        )
    ])
    .sort("row_id")
    .drop("row_id")
)

# Anything else but Notes missing is a problem: drop and report
required = ["Date", "Main Body Part", "Exercise", "Weight", "Reps / Time (Sec.)"]
bad = strength_training_df.filter(pl.any_horizontal(pl.col(required).is_null()))
if bad.height:
    print(f"Dropping {bad.height} row(s) with missing/invalid required values:")
strength_training_df = strength_training_df.drop_nulls(required)

print(bad)

running_df = running_df.with_columns(
    total_miles = pl.col("Miles Ran") + (pl.col("Km Ran") / 1.609),
    total_km = pl.col("Km Ran") + (pl.col("Miles Ran") * 1.609),
).sort("Date").with_columns(
    cumulative_miles = pl.col("total_miles").cum_sum(),
    cumulative_km = pl.col("total_km").cum_sum()
)

strength_training_days = strength_training_df.select("Date").unique().with_columns(workout_type = pl.lit("strength training"))
running_days = running_df.select("Date").unique().with_columns(workout_type = pl.lit("running"))

workout_days_df = (
    pl.concat([strength_training_days, running_days])
    .group_by("Date")
    .agg(
        workout_type=pl.col("workout_type").unique().sort().str.join(" + "),
        workout_types=pl.col("workout_type").n_unique()
    )
    .sort("Date")
)

running_df = (
    running_df.sort("Date")
    .with_columns(cumulative_km=pl.col("total_km").cum_sum())
    .with_columns(
        goal_km=pl.col("Date").dt.ordinal_day()
    )
)

running_df = running_df.with_columns(
    vs_goal = pl.col("cumulative_km")-pl.col("goal_km")
    #vs_goal = pl.col("goal_km")-pl.col("cumulative_km")
)


# ==========================================
# Pages
# ==========================================
with ui.nav_panel("Overview"):
    with ui.layout_sidebar():
        with ui.sidebar():
            ui.input_selectize(
                id = "weight_calc_mode_selectize",
                label = "Reference Weight",
                choices = ["Previous Weigh In", "Start of Selected Time", "Specific Date"],
                selected = "Start of Selected Time"
            )
            @render.ui
            def compare_date_picker():
                if input.weight_calc_mode_selectize() == "Specific Date":
                    workout_dates = (
                        workout_days_df ##TODO make this match up to correct df right now workouts not weigh ins
                        .select(pl.col("Date").unique())
                        .to_series()
                        .to_list()
                    )

                    workout_dates = sorted([
                        d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
                        for d in workout_dates
                    ])

                    return ui.input_date(
                        id="comp_date",
                        label="Comparison Date",
                        value=workout_dates[-1] if workout_dates else None,
                        min=min(workout_dates) if workout_dates else None,
                        max=max(workout_dates) if workout_dates else None,
                        datesdisabled=[
                            d for d in generate_date_range(workout_dates)
                            if d not in workout_dates
                        ]
                    )
                else:
                    return None
        with ui.value_box():
            "Current Weight"
            1
        with ui.value_box():
            "Goal Weight"
            1
        with ui.value_box():
            "Weight Change"
            1
        @render_widget
        def weight_plot():
            return None




# ==========================================
speed_v_distance_toggle = "Distance"
with ui.nav_panel("Running"):
    with ui.layout_sidebar():
        with ui.sidebar():
            ui.input_switch(
                "mile_toggle",
                "Switch View To Miles",
                False
            )
            ui.input_switch(
                "goal_toggle",
                "Hide Goal Line",
                False
            )
            ui.input_switch(
                "speed_v_distance_toggle",
                "Change Distance Metrics To Time"
            )
        with ui.layout_columns(col_widths=[6, 6]):
            with ui.layout_columns(col_widths=[6, 6]):
                with ui.value_box():
                    "Total Runs"
                    running_df.select(pl.len()).item()
                with ui.value_box():
                    "Days Since Last Run"
                    (date.today() - running_df.select(pl.col("Date").max().dt.date()).item()).days
                with ui.value_box():
                    f"Average Run ({speed_v_distance_toggle})"
                    running_df.select(pl.col("total_km").mean()).item()
                with ui.value_box():
                    f"Longest Run ({speed_v_distance_toggle})"
                    running_df.select(pl.col("total_km").max()).item()
                with ui.value_box():
                    "Fastest 1st Mile"
                    running_df.select(pl.col("First Mile Time").min()).item()
                with ui.value_box():
                    "Average First Mile Speed"
                    running_df.select(pl.col("First Mile Time").mean()).item()

            @render.data_frame
            def running_table():
                running_display_df = (
                    running_df
                    .select(
                        pl.col("Date"),
                        pl.col("total_km"),
                        pl.col("Total Time"),
                        pl.col("First Mile Time"),
                        pl.col("Notes"),
                    )
                    .to_pandas()
                )
                return render.DataGrid(running_display_df, editable=False, filters=False)
        with ui.layout_columns():
            @render_widget
            def running_distance_plot():
                    running_distance_fig = px.line(
                        running_df,
                        x="Date",
                        y="cumulative_km",
                        line_shape="hv",
                    )

                    running_distance_fig.update_traces(
                        name="Km ran",
                        hovertemplate="%{y:.2f} km",
                        selector=dict(type="scatter"),
                    )

                    if input.goal_toggle() == False:
                        running_distance_fig.add_scatter(
                            x=running_df["Date"],
                            y=running_df["goal_km"],
                            mode="lines",
                            name="Goal",
                            line_shape="linear",
                            customdata=running_df["vs_goal"],
                            hovertemplate="%{y:.2f} km<br>Difference: %{customdata:+.2f} km",
                        )

                    running_distance_fig.update_layout(
                        hovermode="x unified",
                        xaxis_hoverformat="%b %d, %Y",  # header line, e.g. "Jan 13, 2026"
                        xaxis_title="Date",
                        yaxis_title="Distance (km)",
                        legend_title_text="",
                    )

                    return running_distance_fig

            @render_widget
            def running_goal_plot():
                running_goal_fig = px.line(running_df, x="Date", y="vs_goal")

                running_goal_fig.update_layout(
                    xaxis_hoverformat="%b %d, %Y",  # header line, e.g. "Jan 13, 2026"
                    legend_title_text="",
                    xaxis_title="Date",
                    yaxis_title="km difference",
                )

                return running_goal_fig

    # PLOT A and B (try combining them so that hovering over one shows info on the other

# ==========================================
with ui.nav_panel("Strength Training"):
    with ui.layout_sidebar():
        with ui.sidebar():
            ui.input_switch(
                "overview_v_exercise_toggle",
                "Overview Mode",
                True
            )
            ui.hr()
            ui.h5("Exercise Selection")
            ui.p(
                "Options for selecting exercises have no effect "
                "while Overview Mode is enabled.",
                style="color: grey; font-size: 0.85em;"
            )
            ui.input_selectize(
                id="body_part_selectize",
                label="Primary Body Part",
                choices=strength_training_df["Main Body Part"].unique().to_list(),
                selected="Arms"
            )
            @render.ui
            def exercise_dropdown():
                body_part = input.body_part_selectize()

                exercises = (
                    strength_training_df
                    .filter(pl.col("Main Body Part") == body_part)
                    ["Exercise"].unique().to_list()
                )

                return ui.input_selectize(
                    id="exercise_selectize",
                    label="Exercise",
                    choices=exercises
                )
    "TEMP"
    # with ui.layout_columns():
    #     # Summary Cards - show most common excersise for that
    # with ui.layout_columns():
    #     # PLOT D

        #Workout Data Frame
            # show excersises for that body part then for each days since last done, total sets, sets in window (recent sets), PR weight, max sets at current pr
            # Table body part, days since worked,sets, rank?, excerskse list,

# ==========================================
# Shared filters in the navbar (right-aligned)
# ==========================================
ui.nav_spacer()

with ui.nav_control():
    ui.input_selectize(
        id="year_selectize",
        label=None,
        choices=years,
        selected=str(current_year),
        #width="130px",
    )

with ui.nav_control():
    ui.input_selectize(
        id="month_selectize",
        label=None,
        choices=months,
        selected="YTD",
        #width="110px",
    )