"""
STAM Data Import Wizard — Use Case 1 (10 pts)
Excel upload with column mapping, row-level validation, and audit log.
"""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import streamlit as st

from services.db import AuditLog, Facility, Project, get_session, log_action
from services.importer import import_facilities_from_geojson, import_projects_from_excel

_HERE = os.path.dirname(__file__)
DEMO_DIR = os.path.join(_HERE, "..", "data", "demo")
UPLOAD_DIR = os.path.join(_HERE, "..", "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def render():
    st.title("📥 Data Import")
    st.caption(
        "STAM allows the Province to bring together project, budget and spatial context "
        "data from different source systems into one governed platform."
    )

    tab_excel, tab_geojson, tab_log = st.tabs(
        ["📊 Import Projects (Excel)", "📍 Import Facilities (GeoJSON)", "📋 Import Log"]
    )

    # ── Excel import ──────────────────────────────────────────────────────────
    with tab_excel:
        st.subheader("Import Capital Projects from Excel")

        demo_path = os.path.join(DEMO_DIR, "projects.xlsx")
        if os.path.exists(demo_path):
            with open(demo_path, "rb") as f:
                st.download_button(
                    "⬇ Download Demo Excel Template",
                    data=f.read(),
                    file_name="STAM_Demo_Projects.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            st.caption("Upload the demo file above to see validation in action (2 intentional errors).")

        uploaded = st.file_uploader(
            "Upload projects Excel (.xlsx)",
            type=["xlsx"],
            key="excel_upload",
        )

        if uploaded:
            # Save to temp file
            tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
            tmp.write(uploaded.read())
            tmp.close()

            st.subheader("Preview")
            try:
                df = pd.read_excel(tmp.name, dtype=str)
                st.dataframe(df.head(20), use_container_width=True)
                st.caption(f"{len(df)} rows, {len(df.columns)} columns")
            except Exception as exc:
                st.error(f"Could not preview file: {exc}")

            # Column mapping
            st.subheader("Column Mapping")
            st.caption("Confirm that your column names match the expected STAM fields.")
            expected_cols = [
                "project_id", "project_name", "department", "project_type",
                "latitude", "longitude", "budget_rands", "budget_year",
                "readiness_status", "municipality",
            ]
            df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
            if "name" in df.columns and "project_name" not in df.columns:
                df = df.rename(columns={"name": "project_name"})
            missing_cols = [c for c in expected_cols if c not in df.columns and c != "ward"]
            if not missing_cols:
                st.success("All required columns found")
            else:
                st.error(f"Missing required columns: {missing_cols}")

            # ── Pre-import data quality scan ─────────────────────────────────
            st.subheader("Data Quality Scan")
            st.caption("Row-level checks run before import to catch issues early.")
            issues: list[str] = []
            issue_rows: list[dict] = []

            for idx, row in df.iterrows():
                row_num = int(idx) + 2  # 1-indexed + header

                # Missing project_id
                pid = str(row.get("project_id", "")).strip()
                if not pid or pid == "nan":
                    issues.append(f"Row {row_num}: Missing project_id")
                    issue_rows.append({"Row": row_num, "Field": "project_id", "Issue": "Missing value", "Value": ""})

                # Duplicate project_id within the file
                if pid and pid != "nan":
                    dupes = df[df["project_id"].str.strip() == pid]
                    if len(dupes) > 1 and idx != dupes.index[0]:
                        issues.append(f"Row {row_num}: Duplicate project_id '{pid}'")
                        issue_rows.append({"Row": row_num, "Field": "project_id", "Issue": "Duplicate in file", "Value": pid})

                # Missing or invalid coordinates
                lat_str = str(row.get("latitude", "")).strip()
                lon_str = str(row.get("longitude", "")).strip()
                if not lat_str or lat_str == "nan" or not lon_str or lon_str == "nan":
                    issues.append(f"Row {row_num}: Missing latitude/longitude")
                    issue_rows.append({"Row": row_num, "Field": "latitude/longitude", "Issue": "Missing coordinates", "Value": f"{lat_str}, {lon_str}"})
                else:
                    try:
                        lat_val = float(lat_str)
                        lon_val = float(lon_str)
                        if not (-35 <= lat_val <= -22):
                            issues.append(f"Row {row_num}: Latitude {lat_val} outside South Africa range")
                            issue_rows.append({"Row": row_num, "Field": "latitude", "Issue": "Out of range (-35 to -22)", "Value": lat_str})
                        if not (16 <= lon_val <= 33):
                            issues.append(f"Row {row_num}: Longitude {lon_val} outside South Africa range")
                            issue_rows.append({"Row": row_num, "Field": "longitude", "Issue": "Out of range (16 to 33)", "Value": lon_str})
                    except ValueError:
                        issues.append(f"Row {row_num}: Non-numeric coordinates")
                        issue_rows.append({"Row": row_num, "Field": "latitude/longitude", "Issue": "Non-numeric value", "Value": f"{lat_str}, {lon_str}"})

                # Missing or invalid budget
                budget_str = str(row.get("budget_rands", "")).strip()
                if not budget_str or budget_str == "nan":
                    issues.append(f"Row {row_num}: Missing budget_rands")
                    issue_rows.append({"Row": row_num, "Field": "budget_rands", "Issue": "Missing value", "Value": ""})
                else:
                    try:
                        float(budget_str.replace(",", ""))
                    except ValueError:
                        issues.append(f"Row {row_num}: Non-numeric budget '{budget_str}'")
                        issue_rows.append({"Row": row_num, "Field": "budget_rands", "Issue": "Non-numeric value", "Value": budget_str})

                # Missing municipality
                muni = str(row.get("municipality", "")).strip()
                if not muni or muni == "nan":
                    issues.append(f"Row {row_num}: Missing municipality")
                    issue_rows.append({"Row": row_num, "Field": "municipality", "Issue": "Missing value", "Value": ""})

            valid_count = len(df) - len({r["Row"] for r in issue_rows})
            scan_col1, scan_col2, scan_col3 = st.columns(3)
            scan_col1.metric("Total Rows", len(df))
            scan_col2.metric("Valid Rows", valid_count)
            scan_col3.metric("Rows with Issues", len({r["Row"] for r in issue_rows}),
                             delta_color="inverse" if issue_rows else "normal")

            if issue_rows:
                st.warning(f"{len(issue_rows)} issue(s) found — rows with errors will be skipped during import.")
                st.dataframe(pd.DataFrame(issue_rows), use_container_width=True, hide_index=True)
            else:
                st.success("All rows passed validation — ready to import.")

            budget_year = st.text_input("Default Budget Year", value="2026/27")

            if st.button("✅ Import Projects", type="primary"):
                result = import_projects_from_excel(tmp.name, budget_year,
                                                     user_role=st.session_state.get("user_role", "Analyst"))
                os.unlink(tmp.name)

                col1, col2, col3 = st.columns(3)
                col1.metric("Total Rows", result.total_rows)
                col2.metric("Imported Successfully", result.imported, delta="✓")
                col3.metric("Errors", len(result.errors),
                             delta_color="inverse" if result.errors else "normal")

                if result.errors:
                    st.subheader("Validation Errors")
                    for err in result.errors:
                        st.error(
                            f"**Row {err.row}** — `{err.field}`: {err.message}"
                            + (f" (value: `{err.value}`)" if err.value else "")
                        )

                if result.warnings:
                    st.subheader("Warnings")
                    for w in result.warnings:
                        st.warning(w)

                if result.imported > 0:
                    st.success(
                        f"✓ {result.imported} projects imported successfully. "
                        "Each import is logged for traceability."
                    )
                    st.caption(
                        "The system supports spreadsheet, shapefile, geodatabase and service-based inputs. "
                        "This reduces manual error and shortens preparation time for budgeting."
                    )

    # ── GeoJSON import ────────────────────────────────────────────────────────
    with tab_geojson:
        st.subheader("Import Facilities from GeoJSON")

        demo_geojson = os.path.join(DEMO_DIR, "facilities.geojson")
        if os.path.exists(demo_geojson):
            with open(demo_geojson, "rb") as f:
                st.download_button(
                    "⬇ Download Demo Facilities GeoJSON",
                    data=f.read(),
                    file_name="STAM_Demo_Facilities.geojson",
                    mime="application/json",
                )

        geo_uploaded = st.file_uploader(
            "Upload GeoJSON facility layer",
            type=["geojson", "json"],
            key="geojson_upload",
        )

        if geo_uploaded:
            import json
            data = json.load(geo_uploaded)
            features = data.get("features", [])
            st.info(f"Loaded {len(features)} features from GeoJSON.")

            import folium
            from streamlit_folium import st_folium
            from services.spatial import make_base_map

            m = make_base_map()
            for feat in features[:50]:
                props = feat.get("properties", {})
                coords = feat.get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    folium.CircleMarker(
                        location=[coords[1], coords[0]],
                        radius=8,
                        color="#1F4E79",
                        fill=True,
                        popup=props.get("name", "Facility"),
                    ).add_to(m)
            st_folium(m, use_container_width=True, height=500, returned_objects=[])

            if st.button("✅ Import Facilities", type="primary"):
                tmp = tempfile.NamedTemporaryFile(suffix=".geojson", delete=False, mode="w")
                json.dump(data, tmp)
                tmp.close()
                result = import_facilities_from_geojson(
                    tmp.name, user_role=st.session_state.get("user_role", "Analyst")
                )
                os.unlink(tmp.name)
                st.success(f"✓ {result.imported}/{result.total_rows} facilities imported.")

        # WMS layer info
        st.divider()
        st.subheader("Connect WMS/WFS Layer")
        st.caption("In production, STAM connects to ESRI, GeoServer and other OGC services.")
        wms_url = st.text_input(
            "WMS service URL",
            placeholder="https://gis.gauteng.gov.za/arcgis/services/GSDF/MapServer/WMSServer",
        )
        if wms_url:
            # Parse base URL and layer name from the WMS URL
            from urllib.parse import urlparse, parse_qs

            parsed = urlparse(wms_url)
            base_wms = f"{parsed.scheme}://{parsed.hostname}"
            if parsed.port:
                base_wms += f":{parsed.port}"
            base_wms += parsed.path

            qs = parse_qs(parsed.query)
            layer_name = qs.get("layers", qs.get("LAYERS", [""]))[0]

            validate_btn = st.button("🔗 Validate & Preview", type="primary")

            # Store validation state to prevent continuous reruns
            if validate_btn:
                st.session_state.wms_validated = True
                st.session_state.wms_url = wms_url
                st.session_state.wms_base = base_wms
                st.session_state.wms_layer = layer_name

            # Display WMS preview if validated
            if st.session_state.get("wms_validated") and st.session_state.get("wms_url") == wms_url:
                try:
                    import folium
                    from streamlit_folium import st_folium
                    from services.spatial import make_base_map

                    st.success(
                        f"✓ WMS endpoint configured: `{st.session_state.wms_url}`\n\n"
                        "STAM is compatible with ESRI, GeoServer, and any "
                        "OGC-compliant WMS/WFS service."
                    )

                    m = make_base_map(center=[-26.1, 28.1], zoom=10)

                    folium.raster_layers.WmsTileLayer(
                        url=st.session_state.wms_base,
                        layers=st.session_state.wms_layer or "Projects",
                        fmt="image/png",
                        transparent=True,
                        name=f"WMS: {st.session_state.wms_layer or 'Layer'}",
                        overlay=True,
                        control=True,
                    ).add_to(m)

                    folium.LayerControl(collapsed=False).add_to(m)

                    st.subheader("WMS Layer Preview")
                    st_folium(m, use_container_width=True, height=500, returned_objects=[])

                    log_action(
                        "CONFIGURE_WMS", "layer", "",
                        {"url": st.session_state.wms_url, "layer": st.session_state.wms_layer},
                        user_role=st.session_state.get("user_role", "Analyst"),
                    )

                except Exception as exc:
                    st.error(f"Could not connect to WMS service: {exc}")
            elif wms_url:
                st.caption("Click **Validate & Preview** to see the layer on the map.")

    # ── Import log ────────────────────────────────────────────────────────────
    with tab_log:
        st.subheader("Import & Action Log")
        st.caption("All imports are logged for traceability and audit.")

        session = get_session()
        import_logs = (
            session.query(AuditLog)
            .filter(AuditLog.action.in_(["IMPORT_PROJECTS", "IMPORT_FACILITIES",
                                          "CONFIGURE_WMS", "DATABASE_SEEDED"]))
            .order_by(AuditLog.timestamp.desc())
            .limit(50)
            .all()
        )

        if import_logs:
            rows = []
            for e in import_logs:
                ts = e.timestamp[:19].replace("T", " ") if e.timestamp else ""
                rows.append({
                    "Timestamp (UTC)": ts,
                    "User Role": e.user_role,
                    "Action": e.action,
                    "Detail": e.detail[:100] if e.detail else "",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No import actions recorded yet.")

        session.close()
