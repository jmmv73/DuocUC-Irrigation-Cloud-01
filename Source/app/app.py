"""HTTP dashboard for an intentionally simple classroom demonstration."""
from datetime import timezone

from dash import Dash, Input, Output, State, dcc, html
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from waitress import serve

import core

app = Dash(__name__, title=core.CONFIG["dashboard"]["title"])
server = app.server

COLORS = {"soil_moisture": "#60a5fa", "temperature": "#fbbf24", "soil_ec": "#34d399", "water_ph": "#c084fc"}
BG = "#0b1220"
PANEL = {"background": "#172132", "border": "1px solid #334155", "borderRadius": "12px", "padding": "17px"}


def cell_input(sensor, name, bound, value):
    return dcc.Input(id=f"{sensor}-{bound}", type="number", step="any", value=value,
                     style={"width": "100%", "maxWidth": "120px", "background": "#0f172a", "color": "white", "border": "1px solid #64748b", "padding": "9px", "borderRadius": "5px"})


def layout():
    rules = core.get_rules()
    rows = []
    for key, (name, unit) in core.SENSORS.items():
        rows.append(html.Tr([
            html.Td(name, style={"padding": "10px"}),
            html.Td(unit, style={"padding": "10px"}),
            html.Td(cell_input(key, name, "min", rules[key]["min"]), style={"padding": "10px"}),
            html.Td(cell_input(key, name, "max", rules[key]["max"]), style={"padding": "10px"}),
        ]))
    return html.Div([
        html.Div([
            html.H1(core.CONFIG["dashboard"]["title"], style={"marginBottom": "4px"}),
            html.P("Live sensors, history and bidirectional MQTT alarms", style={"color": "#94a3b8"}),
            html.Div(id="connection-status", style={"color": "#a7f3d0", "padding": "9px 0 16px"}),
            html.Div(id="sensor-cards", style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit,minmax(190px,1fr))", "gap": "12px"}),
            html.Div([
                html.H2("Historical trends", style={"marginBottom": "12px"}),
                dcc.Dropdown(id="history-hours", clearable=False, value=core.CONFIG["dashboard"]["history_hours"],
                             options=[{"label": label, "value": hours} for label, hours in [("1 hour", 1), ("6 hours", 6), ("24 hours", 24), ("7 days", 168), ("30 days", 720)]],
                             style={"maxWidth": "240px", "color": "#111827"}),
                dcc.Graph(id="history-graph", config={"displaylogo": False}),
                html.Div(id="history-info", style={"color": "#94a3b8", "fontSize": "13px"}),
            ], style={**PANEL, "marginTop": "18px"}),
            html.Div([
                html.H2("Alarm thresholds"),
                html.P("Limits are persisted in MongoDB. Changing them immediately re-evaluates alarms.", style={"color": "#94a3b8"}),
                html.Div(html.Table([
                    html.Thead(html.Tr([html.Th(x, style={"padding": "10px", "textAlign": "left"}) for x in ["Sensor", "Unit", "Minimum", "Maximum"]])),
                    html.Tbody(rows)
                ], style={"width": "100%", "minWidth": "430px", "borderCollapse": "collapse"}), style={"overflowX": "auto"}),
                html.Button("Save thresholds", id="save-thresholds", n_clicks=0,
                            style={"padding": "12px 20px", "marginTop": "16px", "borderRadius": "8px", "background": "#16a34a", "color": "white", "border": "0", "cursor": "pointer", "fontWeight": "bold"}),
                html.Div(id="save-result", style={"marginTop": "10px"}),
            ], style={**PANEL, "marginTop": "18px"}),
            html.Div([html.H2("Recent alarm transitions"), html.Div(id="alarm-events")], style={**PANEL, "marginTop": "18px"}),
            html.P(f"Device: {core.DEVICE_ID} | Telemetry: {core.TOPICS['telemetry']} | Alarms: {core.TOPICS['alarms']}",
                   style={"fontSize": "12px", "color": "#94a3b8", "marginTop": "18px", "overflowWrap": "anywhere"}),
            dcc.Interval(id="refresh", interval=int(core.CONFIG["dashboard"]["refresh_ms"]), n_intervals=0),
        ], style={"maxWidth": "1200px", "margin": "auto", "padding": "18px"})
    ], style={"fontFamily": "system-ui,Arial,sans-serif", "background": BG, "color": "#f1f5f9", "minHeight": "100vh"})


app.layout = layout


@app.callback(
    Output("sensor-cards", "children"), Output("history-graph", "figure"),
    Output("connection-status", "children"), Output("history-info", "children"),
    Output("alarm-events", "children"),
    Input("refresh", "n_intervals"), Input("history-hours", "value")
)
def refresh(_n, hours):
    measurement, rules, alarm = core.snapshot()
    cards = []
    for key, (name, unit) in core.SENSORS.items():
        value = measurement[key] if measurement else None
        state = alarm["states"][key]
        color = "#34d399" if state == "NORMAL" else "#94a3b8" if state == "UNKNOWN" else "#fb7185"
        cards.append(html.Div([
            html.Div(name, style={"color": "#94a3b8", "fontSize": "14px"}),
            html.Div(f"{value:.2f} {unit}" if value is not None else "—",
                     style={"fontSize": "28px", "fontWeight": "bold", "margin": "7px 0", "color": color}),
            html.Div(state, style={"fontWeight": "bold", "color": color}),
            html.Div(f"Min: {rules[key]['min']}  ·  Max: {rules[key]['max']}", style={"fontSize": "12px", "marginTop": "7px", "color": "#cbd5e1"}),
        ], style=PANEL))
    history = core.get_history(hours=hours)
    fig = make_subplots(rows=2, cols=2, subplot_titles=[name for name, _ in core.SENSORS.values()], vertical_spacing=.18)
    times = [r["received_at"] for r in history]
    for index, (key, (_name, unit)) in enumerate(core.SENSORS.items()):
        row, col = index // 2 + 1, index % 2 + 1
        fig.add_trace(go.Scatter(x=times, y=[r[key] for r in history], name=key, mode="lines+markers",
                                 marker={"size": 3}, line={"color": COLORS[key], "width": 2}), row=row, col=col)
        for boundary in ("min", "max"):
            fig.add_hline(y=rules[key][boundary], line_dash="dot", line_color="#fb7185", opacity=.8, row=row, col=col)
        fig.update_yaxes(title_text=unit, row=row, col=col)
    fig.update_layout(height=640, showlegend=False, paper_bgcolor="#172132", plot_bgcolor="#172132",
                      font={"color": "#dbeafe"}, margin={"l": 55, "r": 15, "t": 55, "b": 45})
    fig.update_xaxes(showgrid=True, gridcolor="#334155")
    fig.update_yaxes(showgrid=True, gridcolor="#334155")
    mqtt_state = "MQTT ONLINE" if core.client.is_connected() else "MQTT OFFLINE"
    sensor_state = "NO RECENT DATA" if alarm["stale"] else "ALARM ACTIVE" if alarm["active"] else "ALL NORMAL"
    status = f"{mqtt_state}  •  {sensor_state}  •  {core.DEVICE_ID}"
    hist_info = f"{len(history)} most recent points in the selected window (limit {core.CONFIG['dashboard']['max_graph_points']}). Times are UTC; red dotted lines show current thresholds."
    events = []
    for ev in core.get_events(12):
        when = ev["time"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        abnormal = ", ".join(f"{key}: {v}" for key, v in ev["states"].items() if v != "NORMAL") or "normal"
        events.append(html.Div(f"{when} — {abnormal}", style={"borderBottom": "1px solid #334155", "padding": "8px 0", "fontSize": "13px"}))
    return cards, fig, status, hist_info, events or html.P("No alarm transitions yet")


states = [State(f"{sensor}-{boundary}", "value") for sensor in core.SENSORS for boundary in ("min", "max")]


@app.callback(Output("save-result", "children"), Input("save-thresholds", "n_clicks"), *states, prevent_initial_call=True)
def save_thresholds(_clicks, *values):
    try:
        rules = {sensor: {"min": values[i * 2], "max": values[i * 2 + 1]}
                 for i, sensor in enumerate(core.SENSORS)}
        core.update_rules(rules)
        note = "Thresholds saved. " + ("MQTT published." if core.client.is_connected() else "MQTT offline; will resync on reconnect.")
        return html.Span(note, style={"color": "#34d399"})
    except (ValueError, TypeError, KeyError) as exc:
        return html.Span(str(exc), style={"color": "#fb7185"})
    except Exception:
        core.LOG.exception("Failed to save rules")
        return html.Span("Database or server error", style={"color": "#fb7185"})


if __name__ == "__main__":
    core.start_services()
    serve(server, host="0.0.0.0", port=8080, threads=4)
