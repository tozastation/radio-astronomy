#!/usr/bin/env python3
"""
🛰️ Satellite Observation Viewer (Garage S3 Dashboard)
Ultra-lightweight web dashboard for mobile and desktop browsing.
"""

import os
import re
import json
import urllib.parse
import threading
import asyncio
import time
import tarfile
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import boto3
from botocore.client import Config
from temporalio.client import Client

from orbit_calculator import OrbitCalculator, load_tle_for_satellite, DEFAULT_TARGET_TLES

S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://garage-s3.storage.svc.cluster.local:3900")
S3_BUCKET = os.environ.get("S3_BUCKET", "satellite-recordings")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))
TEMPORAL_HOST = os.environ.get("TEMPORAL_HOST", "temporal-server.temporal.svc.cluster.local:7233")
TASK_QUEUE = "satellite-analysis"
ENABLE_AUTO_DISPATCH = os.environ.get("ENABLE_AUTO_DISPATCH", "true").lower() in ["true", "1", "yes"]

triggered_passes = set()

s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name="garage",
    config=Config(s3={"addressing_style": "path"})
)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>🛰️ 衛星電波観測ダッシュボード (Garage S3)</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.2);
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --success: #34d399;
      --warning: #fbbf24;
      --code-bg: #0b1120;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      padding: 16px;
      padding-bottom: max(60px, env(safe-area-inset-bottom, 60px));
      -webkit-font-smoothing: antialiased;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border);
      gap: 12px;
    }
    .header-title-block {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .header-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    h1 {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--accent);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .badge-bucket {
      background: #0284c7;
      color: white;
      font-size: 0.75rem;
      padding: 3px 8px;
      border-radius: 999px;
      font-weight: 600;
    }
    .btn-refresh {
      background: var(--card-bg);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.85rem;
      cursor: pointer;
      white-space: nowrap;
      transition: all 0.15s ease;
      touch-action: manipulation;
    }
    .btn-refresh:active { background: var(--border); }
    .btn-backup {
      background: #059669;
      color: white;
      border-color: #34d399;
    }
    .btn-backup:active { background: #047857; }
    .btn-passes {
      background: #0284c7;
      color: white;
      border-color: #38bdf8;
    }
    .btn-passes:active { background: #0369a1; }
    .btn-reload {
      background: var(--card-bg);
      color: var(--text);
      border-color: var(--border);
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
      margin-bottom: 16px;
    }
    .card-header {
      padding: 12px 16px;
      background: rgba(255, 255, 255, 0.03);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      border-bottom: 1px solid var(--border);
      gap: 12px;
    }
    .sat-header-left {
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
      flex: 1 1 auto;
    }
    .sat-name { font-weight: 700; font-size: 1.1rem; color: #38bdf8; word-break: break-word; }
    .sat-badges { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
    .pass-id {
      font-family: monospace;
      font-size: 0.72rem;
      color: var(--text-muted);
      word-break: break-all;
      margin-top: 2px;
    }
    .sat-header-right {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-shrink: 0;
    }
    .btn-replay {
      background: linear-gradient(135deg, #0284c7, #0369a1);
      color: white;
      border: 1px solid #38bdf8;
      font-weight: bold;
      padding: 5px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.8rem;
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      touch-action: manipulation;
    }
    .btn-replay:hover {
      background: linear-gradient(135deg, #0369a1, #0284c7);
    }
    .badge-tag {
      font-size: 0.7rem;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 600;
    }
    .badge-spacestation { background: #0369a1; color: #e0f2fe; }
    .badge-weathersatellite { background: #047857; color: #d1fae5; }
    .badge-cubesat { background: #6d28d9; color: #ede9fe; }
    .badge-genericsatellite { background: #334155; color: #f1f5f9; }
    .badge-freq { background: #1e1b4b; color: #c7d2fe; border: 1px solid #3730a3; }
    .badge-signal { background: #312e81; color: #e0e7ff; }
    .summary-box {
      background: rgba(56, 189, 248, 0.08);
      border-left: 3px solid var(--accent);
      padding: 8px 12px;
      border-radius: 4px;
      font-size: 0.82rem;
      color: #bae6fd;
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .badge-status {
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(52, 211, 153, 0.15);
      color: var(--success);
      border: 1px solid rgba(52, 211, 153, 0.3);
    }
    .card-body { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
    .spectrogram-container {
      position: relative;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #000;
      text-align: center;
    }
    .spectrogram-img {
      width: 100%;
      height: auto;
      max-height: 280px;
      object-fit: contain;
      display: block;
      cursor: pointer;
    }
    .spectrogram-hint {
      position: absolute;
      bottom: 6px;
      right: 8px;
      background: rgba(0,0,0,0.7);
      color: #fff;
      font-size: 0.7rem;
      padding: 3px 8px;
      border-radius: 4px;
      pointer-events: none;
    }
    .meta-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      background: rgba(0, 0, 0, 0.2);
      padding: 10px;
      border-radius: 8px;
      font-size: 0.85rem;
    }
    .meta-item { display: flex; flex-direction: column; }
    .meta-label { color: var(--text-muted); font-size: 0.7rem; }
    .meta-val { font-weight: 600; margin-top: 2px; }

    /* JSON Details & Code View */
    details.data-accordion {
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }
    details.data-accordion summary {
      padding: 10px 12px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      color: var(--text);
      display: flex;
      justify-content: space-between;
      align-items: center;
      user-select: none;
    }
    details.data-accordion summary:hover {
      background: rgba(255, 255, 255, 0.03);
    }
    .accordion-content {
      padding: 12px;
      background: var(--code-bg);
      border-top: 1px solid var(--border);
    }
    pre.code-block {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.75rem;
      color: #38bdf8;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-all;
      max-height: 250px;
      padding: 4px 0;
    }

    /* File Actions */
    .files-section {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .files-title {
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--text-muted);
    }
    .files-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    @media (min-width: 640px) {
      .files-grid { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    }
    .file-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 0.8rem;
      gap: 8px;
    }
    .file-name {
      font-family: monospace;
      font-weight: 600;
      color: #e2e8f0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
      flex: 1 1 auto;
    }
    .file-actions {
      display: flex;
      gap: 6px;
      flex-shrink: 0;
    }
    .action-btn {
      padding: 5px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: none;
      white-space: nowrap;
      touch-action: manipulation;
    }
    .btn-view {
      background: #0284c7;
      color: white;
    }
    .btn-view:active { background: #0369a1; }
    .btn-download {
      background: #334155;
      color: #e2e8f0;
    }
    .btn-download:active { background: #475569; }

    /* Modals */
    .modal {
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.85);
      backdrop-filter: blur(4px);
      z-index: 9999;
      justify-content: center;
      align-items: center;
      padding: 16px;
    }
    .modal.active { display: flex; }
    .modal-box {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      width: 100%;
      max-width: 650px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
      overflow: hidden;
    }
    .modal-header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-title {
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--accent);
      font-family: monospace;
    }
    .modal-close {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 24px;
      cursor: pointer;
      line-height: 1;
    }
    .modal-body {
      padding: 16px;
      overflow-y: auto;
      flex: 1;
    }
    .modal-img {
      max-width: 100%;
      max-height: 75vh;
      object-fit: contain;
      display: block;
      margin: 0 auto;
    }
    .modal-footer {
      padding: 10px 16px;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }
    .copy-btn {
      background: #334155;
      color: white;
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.8rem;
      cursor: pointer;
    }
    .empty-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--text-muted);
    }
    .no-match-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--text-muted);
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      margin-bottom: 20px;
      display: none;
    }

    /* Toolbar & Filter Styles */
    .toolbar-section {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    .filter-chips {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .chip {
      background: rgba(255, 255, 255, 0.04);
      color: var(--text-muted);
      border: 1px solid var(--border);
      padding: 6px 14px;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      user-select: none;
      touch-action: manipulation;
    }
    .chip:hover {
      background: rgba(255, 255, 255, 0.08);
      color: var(--text);
    }
    .chip.active {
      background: #0284c7;
      color: #fff;
      border-color: var(--accent);
      box-shadow: 0 0 10px var(--accent-glow);
    }
    .filter-options {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.85rem;
      padding-top: 8px;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
    }
    .toggle-label {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      color: var(--text);
      user-select: none;
      font-weight: 500;
    }
    .toggle-label input[type="checkbox"] {
      width: 17px;
      height: 17px;
      accent-color: var(--accent);
      cursor: pointer;
    }
    .matched-count {
      color: var(--text-muted);
      font-size: 0.8rem;
      font-family: monospace;
    }
    .scroll-loader {
      text-align: center;
      padding: 24px;
      color: var(--accent);
      font-size: 0.9rem;
      display: none;
      align-items: center;
      justify-content: center;
      gap: 10px;
    }
    .scroll-loader.active {
      display: flex;
    }
    .spinner {
      width: 20px;
      height: 20px;
      border: 3px solid rgba(56, 189, 248, 0.2);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    /* Replay Modal & Radar Styles */
    .modal-box-large {
      max-width: 980px;
      width: 95vw;
    }
    .replay-layout {
      display: grid;
      grid-template-columns: 440px 1fr;
      gap: 20px;
    }
    @media (max-width: 860px) {
      .replay-layout {
        grid-template-columns: 1fr;
      }
    }
    .radar-wrapper {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
    }
    .radar-canvas {
      width: 100%;
      max-width: 440px;
      aspect-ratio: 1 / 1;
      background: #080e1a;
      border-radius: 12px;
      border: 1px solid #334155;
      box-shadow: 0 0 20px rgba(6, 182, 212, 0.15);
      display: block;
      margin: 0 auto;
    }
    .radar-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 0.78rem;
      justify-content: center;
    }
    .legend-item {
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .legend-color {
      width: 12px;
      height: 12px;
      border-radius: 3px;
    }
    .telemetry-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
    }
    .telemetry-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      font-size: 0.85rem;
    }
    .telemetry-item {
      display: flex;
      flex-direction: column;
    }
    .telemetry-label {
      font-size: 0.72rem;
      color: var(--text-muted);
    }
    .telemetry-val {
      font-weight: 700;
      color: var(--text);
      font-family: monospace;
      font-size: 0.95rem;
    }
    .spectrogram-wrapper {
      position: relative;
      cursor: crosshair;
      border-radius: 6px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #000;
    }
    .spectrogram-wrapper img {
      width: 100%;
      display: block;
      height: auto;
    }
    .spectrogram-cursor {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 2px;
      background: #ef4444;
      box-shadow: 0 0 8px #ef4444;
      pointer-events: none;
      left: 0%;
      transition: left 0.05s linear;
    }
    .clipper-card {
      background: rgba(6, 182, 212, 0.05);
      border: 1px solid rgba(6, 182, 212, 0.3);
      border-radius: 8px;
      padding: 12px;
      margin-top: 12px;
    }
    .clipper-title {
      font-size: 0.85rem;
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .clipper-controls {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 8px;
    }
    .clipper-inputs-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .clipper-input-group {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 0.82rem;
      background: rgba(0, 0, 0, 0.2);
      padding: 4px 8px;
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .clipper-input-group input[type="number"] {
      background: #0b1120;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 4px 6px;
      border-radius: 4px;
      width: 50px;
      font-family: monospace;
      flex: 1;
    }
    .btn-auto-window {
      background: #0891b2;
      color: white;
      font-weight: bold;
      width: 100%;
      padding: 6px 10px;
      border-radius: 6px;
      border: 1px solid #06b6d4;
      font-size: 0.8rem;
      cursor: pointer;
    }
    .btn-auto-window:hover {
      background: #0e7490;
    }
    .btn-generate-gif {
      background: linear-gradient(135deg, #059669, #047857);
      color: white;
      font-weight: bold;
      width: 100%;
      padding: 8px;
      border-radius: 6px;
      font-size: 0.88rem;
      cursor: pointer;
      border: none;
    }
    .btn-generate-gif:active {
      filter: brightness(0.9);
    }
    .btn-sm {
      background: #334155;
      color: var(--text);
      border: 1px solid var(--border);
      padding: 4px 8px;
      border-radius: 4px;
      font-size: 0.75rem;
      cursor: pointer;
    }
    .btn-sm:hover { background: #475569; }
    .progress-bar-container {
      margin-top: 8px;
      background: #0b1120;
      border-radius: 4px;
      overflow: hidden;
      height: 8px;
      display: none;
    }
    .progress-bar-fill {
      height: 100%;
      background: #10b981;
      width: 0%;
      transition: width 0.1s linear;
    }

    /* Backup & Restore Layout */
    .backup-stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 8px;
      font-size: 0.85rem;
      color: #cbd5e1;
    }
    .restore-input-row {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }

    /* Responsive Mobile Optimizations (< 640px) */
    @media (max-width: 640px) {
      body {
        padding: 10px;
        padding-bottom: max(60px, env(safe-area-inset-bottom, 60px));
      }
      header {
        flex-direction: column;
        align-items: stretch;
        gap: 12px;
        margin-bottom: 14px;
      }
      .header-title-block {
        justify-content: space-between;
      }
      h1 {
        font-size: 1.15rem;
      }
      .header-actions {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 6px;
        width: 100%;
      }
      .header-actions .btn-refresh {
        padding: 8px 4px;
        font-size: 0.74rem;
        text-align: center;
        white-space: normal;
        line-height: 1.25;
        min-height: 38px;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .toolbar-section {
        padding: 12px;
        gap: 10px;
        margin-bottom: 14px;
      }
      .filter-chips {
        gap: 6px;
      }
      .chip {
        padding: 6px 11px;
        font-size: 0.78rem;
      }
      .filter-options {
        flex-direction: column;
        align-items: flex-start;
        gap: 6px;
      }
      .matched-count {
        font-size: 0.75rem;
      }
      .card-header {
        flex-direction: column;
        align-items: stretch;
        gap: 10px;
        padding: 12px;
      }
      .sat-name {
        font-size: 1.02rem;
      }
      .sat-header-right {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
        width: 100%;
        padding-top: 6px;
        border-top: 1px dashed rgba(255, 255, 255, 0.08);
      }
      .sat-header-right .btn-replay {
        flex: 1;
        padding: 8px 12px;
        font-size: 0.82rem;
      }
      .card-body {
        padding: 12px;
        gap: 12px;
      }
      .modal {
        padding: 8px;
      }
      .modal-box {
        max-height: 94vh;
        border-radius: 10px;
      }
      .modal-header {
        padding: 10px 12px;
      }
      .modal-title {
        font-size: 0.88rem;
      }
      .modal-body {
        padding: 10px;
      }
      .modal-footer {
        padding: 8px 12px;
      }
      .radar-canvas {
        max-width: 290px;
      }
      .radar-wrapper {
        gap: 8px;
      }
      .radar-legend {
        font-size: 0.72rem;
        gap: 6px;
      }
      .telemetry-grid {
        gap: 6px;
        font-size: 0.8rem;
      }
      .telemetry-val {
        font-size: 0.85rem;
      }
      .clipper-inputs-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-title-block">
      <h1>🛰️ 衛星電波観測ビューア</h1>
      <span class="badge-bucket">Garage S3: __BUCKET__</span>
    </div>
    <div class="header-actions">
      <button class="btn-refresh btn-backup" onclick="openBackupModal()">📦 バックアップ ＆ 復元</button>
      <button class="btn-refresh btn-passes" onclick="openFuturePassesModal()">📅 通過予測 (24h)</button>
      <button class="btn-refresh btn-reload" onclick="loadPasses(1, true)">🔄 最新データ取得</button>
    </div>
  </header>

  <!-- Filter & Search Toolbar -->
  <div class="toolbar-section">
    <div class="filter-chips">
      <button class="chip active" onclick="setCategoryFilter('all', this)">すべて (<span id="count-all">0</span>)</button>
      <button class="chip" onclick="setCategoryFilter('SpaceStation', this)">🚀 ISS (<span id="count-spacestation">0</span>)</button>
      <button class="chip" onclick="setCategoryFilter('WeatherSatellite', this)">🛰️ METEOR (<span id="count-weathersatellite">0</span>)</button>
      <button class="chip" onclick="setCategoryFilter('CubeSat', this)">📻 FUNcube (<span id="count-cubesat">0</span>)</button>
    </div>
    <div class="filter-options">
      <label class="toggle-label">
        <input type="checkbox" id="hasPacketsToggle" onchange="applyFilters()">
        <span>📡 パケット/データ検出ありのみ絞り込み</span>
      </label>
      <span class="matched-count" id="matchedCount">読み込み中...</span>
    </div>
  </div>

  <div id="noMatchState" class="no-match-state">条件に一致する観測データがありません。</div>

  <main id="cardsContainer"></main>
  <div id="scrollSentinel" style="height: 20px; margin: 10px 0;"></div>
  <div id="scrollLoader" class="scroll-loader">
    <div class="spinner"></div>
    <span>観測データを読み込み中...</span>
  </div>

  <!-- Image Modal -->
  <div id="imageModal" class="modal" onclick="closeImageModal(event)">
    <div class="modal-box" style="background:transparent; border:none; box-shadow:none; max-width:95vw;">
      <div style="text-align:right; margin-bottom:8px;">
        <button class="modal-close" style="color:white; font-size:32px;" onclick="document.getElementById('imageModal').classList.remove('active')">&times;</button>
      </div>
      <img id="modalImg" class="modal-img" src="" alt="Spectrogram Full">
    </div>
  </div>

  <!-- Text / JSON Modal -->
  <div id="textModal" class="modal" onclick="closeTextModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
      <div class="modal-header">
        <span id="textModalTitle" class="modal-title">File Content</span>
        <button class="modal-close" onclick="closeTextModal()">&times;</button>
      </div>
      <div class="modal-body">
        <pre class="code-block"><code id="textModalContent">Loading...</code></pre>
      </div>
      <div class="modal-footer">
        <button class="copy-btn" onclick="copyModalContent()">📋 コピー</button>
        <button class="copy-btn" onclick="closeTextModal()">閉じる</button>
      </div>
    </div>
  </div>

  <!-- Future Passes Modal -->
  <div id="futurePassesModal" class="modal" onclick="closeFuturePassesModal(event)">
    <div class="modal-box modal-box-large" onclick="event.stopPropagation()">
      <div class="modal-header">
        <span class="modal-title">📅 今後の北向きベランダ通過予測 (24時間)</span>
        <button class="modal-close" onclick="closeFuturePassesModal()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="background:rgba(6,182,212,0.1); border:1px solid rgba(6,182,212,0.3); border-radius:8px; padding:10px 14px; font-size:0.83rem; margin-bottom:14px; color:#e2e8f0;">
          💡 <strong>ベランダ観測可能ウィンドウ</strong>: 方位角 250°〜110°（西〜北〜東）、仰角 25°〜60°（手すり下限〜上階庇上限）。南側建物遮蔽を避け、この開口窓を通過するパスを SGP4 により高精度予測しています。
        </div>
        <div id="futurePassesList">
          <div style="text-align:center; padding:20px; color:#94a3b8;">読み込み中...</div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="copy-btn" onclick="closeFuturePassesModal()">閉じる</button>
      </div>
    </div>
  </div>

  <!-- Backup & Restore Modal -->
  <div id="backupModal" class="modal" onclick="closeBackupModal(event)">
    <div class="modal-box" style="max-width: 640px;" onclick="event.stopPropagation()">
      <div class="modal-header">
        <span class="modal-title">📦 S3 フルバックアップ ＆ ディザスタリカバリ</span>
        <button class="modal-close" onclick="closeBackupModal()">&times;</button>
      </div>
      <div class="modal-body" style="padding: 16px; font-size: 0.9rem; line-height: 1.6;">
        <div style="background: rgba(30, 41, 59, 0.7); border: 1px solid #334155; border-radius: 8px; padding: 12px; margin-bottom: 16px;">
          <div style="font-weight: bold; color: var(--accent); margin-bottom: 6px;">📊 Garage S3 ストレージ統計</div>
          <div id="backupStatsLoading">ストレージ情報を取得中...</div>
          <div id="backupStatsContent" style="display: none;">
            <div class="backup-stats-grid">
              <div>総オブジェクト数: <strong id="statTotalCount" style="color:white;">-</strong> 件</div>
              <div>総ストレージ容量: <strong id="statTotalSize" style="color:white;">-</strong></div>
              <div>成果物 (results/): <span id="statResultsCount" style="color:#38bdf8;">-</span> 件</div>
              <div>生データ (archive/): <span id="statArchiveCount" style="color:#a78bfa;">-</span> 件</div>
              <div>ゴールデンRAW (golden/): <span id="statGoldenCount" style="color:#f59e0b;">-</span> 件</div>
              <div>一時受取 (raw/): <span id="statRawCount" style="color:#94a3b8;">-</span> 件</div>
            </div>
          </div>
        </div>

        <div style="margin-bottom: 20px;">
          <h4 style="margin: 0 0 8px 0; color: #38bdf8;">📥 1. フルバックアップのダウンロード</h4>
          <p style="color: #94a3b8; font-size: 0.82rem; margin: 0 0 10px 0;">
            Garage S3 内の全成果物（画像・JSON・スペクトログラム）および生 IQ / WAV 音声を単一の <code>.tar</code> アーカイブとしてストリーミング配信します。<br>
            ダウンロード完了後、Google Drive 等の安全な外部クラウドストレージへ手動で保管してください。
          </p>
          <a id="btnDownloadBackup" href="/api/backup/download" class="action-btn" style="display: inline-block; background: #059669; color: white; padding: 8px 16px; font-weight: bold; text-decoration: none; border-radius: 6px; cursor: pointer;">
            💾 フルバックアップ (.tar) をダウンロード
          </a>
        </div>

        <hr style="border: none; border-top: 1px solid #334155; margin: 20px 0;">

        <div>
          <h4 style="margin: 0 0 8px 0; color: #f59e0b;">📤 2. バックアップからの復元（リストア）</h4>
          <p style="color: #94a3b8; font-size: 0.82rem; margin: 0 0 10px 0;">
            Google Drive 等から取得したバックアップ <code>.tar</code> ファイルを選択すると、Garage S3 へ全データを展開・復元します。<br>
            ※ 数十GB 以上の巨大バックアップ時は、ブラウザタイムアウトを防ぐため同梱の CLI ツール（<code>scripts/restore_backup.py</code>）のご利用を推奨します。
          </p>
          <div class="restore-input-row">
            <input type="file" id="restoreFileInput" accept=".tar,.tar.gz" style="color: #94a3b8; font-size: 0.85rem; max-width: 100%;">
            <button id="btnExecuteRestore" class="action-btn" style="background: #f59e0b; color: white; padding: 6px 14px; font-weight: bold; border-radius: 6px; cursor: pointer; white-space: nowrap;" onclick="executeRestore()">復元実行</button>
          </div>
          <div id="restoreStatus" style="font-size: 0.82rem; color: #38bdf8; min-height: 18px;"></div>
        </div>
      </div>
      <div class="modal-footer" style="padding: 12px 16px; border-top: 1px solid #334155; text-align: right;">
        <button class="copy-btn" onclick="closeBackupModal()">閉じる</button>
      </div>
    </div>
  </div>

  <!-- Satellite Pass Replay & GIF Clipper Modal -->
  <div id="replayModal" class="modal" onclick="closeReplayModal(event)">
    <div class="modal-box modal-box-large" onclick="event.stopPropagation()">
      <div class="modal-header">
        <span class="modal-title" id="replayModalTitle">🛰️ 天球観測リプレイ ＆ GIF 切り出し</span>
        <button class="modal-close" onclick="closeReplayModal()">&times;</button>
      </div>
      <div class="modal-body">
        <div class="replay-layout">
          <!-- Left: Sky Dome Radar -->
          <div class="radar-wrapper">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <span style="font-size:0.8rem; font-weight:bold; color:#cbd5e1;">2D 天球レーダー</span>
              <button id="btnToggleHeatmap" class="btn-sm" onclick="toggleSkyHeatmap()" style="padding:2px 8px; font-size:0.75rem; background:#1e293b; border:1px solid #334155; color:#94a3b8; border-radius:4px; cursor:pointer;">🎯 実績ヒートマップ: OFF</button>
            </div>
            <canvas id="radarCanvas" width="440" height="440" class="radar-canvas"></canvas>
            <div class="radar-legend">
              <div class="legend-item"><div class="legend-color" style="background:#06b6d4;"></div><span>開口窓 (Az 250°~110°, El 25°~60°)</span></div>
              <div class="legend-item"><div class="legend-color" style="background:rgba(16,185,129,0.5); border:1px dashed #10b981;"></div><span>復調実績ヒートマップ</span></div>
              <div class="legend-item"><div class="legend-color" style="background:rgba(15,23,42,0.85); border:1px solid #ef4444;"></div><span>南側建物遮蔽</span></div>
              <div class="legend-item"><div class="legend-color" style="background:#34d399; border-radius:50%;"></div><span>衛星位置</span></div>
            </div>
          </div>

          <!-- Right: Telemetry, Replay Controls, Decoded Image & GIF Clipper -->
          <div>
            <!-- Status Badge & Telemetry -->
            <div class="telemetry-card">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span id="repSatLabel" style="font-weight:bold; color:#38bdf8; font-size:1.05rem;">🛰️ 観測パス</span>
                <span id="windowStatusBadge" style="padding:2px 8px; border-radius:999px; font-size:0.75rem; font-weight:bold; background:#1e293b; color:#94a3b8; border:1px solid #334155;">判定中...</span>
              </div>
              <div class="telemetry-grid">
                <div class="telemetry-item">
                  <span class="telemetry-label">経過時刻 / JST</span>
                  <span class="telemetry-val" id="repTimeJst">--:--:--</span>
                </div>
                <div class="telemetry-item">
                  <span class="telemetry-label">方位角 (Azimuth)</span>
                  <span class="telemetry-val" id="repAz">--°</span>
                </div>
                <div class="telemetry-item">
                  <span class="telemetry-label">仰角 (Elevation)</span>
                  <span class="telemetry-val" id="repEl">--°</span>
                </div>
                <div class="telemetry-item">
                  <span class="telemetry-label">スラント距離 (Range)</span>
                  <span class="telemetry-val" id="repRange">-- km</span>
                </div>
              </div>
            </div>

            <!-- Decoded Cloud Image Preview (pops up during lock) -->
            <div id="replayDecodedBox" style="display:none; background:rgba(56,189,248,0.08); border:1px solid rgba(56,189,248,0.3); border-radius:8px; padding:8px 12px; margin-bottom:12px;">
              <div style="font-size:0.75rem; font-weight:bold; color:#38bdf8; margin-bottom:4px; display:flex; justify-content:space-between;">
                <span>🛰️ 復調気象画像 (CADU ロック)</span>
                <span style="font-size:0.7rem; color:#94a3b8;">タップで拡大</span>
              </div>
              <img id="replayDecodedImg" src="" style="width:100%; max-height:140px; object-fit:cover; border-radius:4px; cursor:pointer;" onclick="openImageModal(this.src)">
            </div>

            <!-- Spectrogram with Synced Red Cursor -->
            <div style="margin-bottom:8px;">
              <div style="display:flex; justify-content:space-between; font-size:0.75rem; color:#94a3b8; margin-bottom:4px;">
                <span>📊 受信スペクトログラム (クリックでシーク)</span>
                <span id="repCurrentTimeText">00:00 / 00:00</span>
              </div>
              <div id="replaySpectrogramContainer" class="spectrogram-wrapper" onclick="handleSpectrogramClick(event)">
                <img id="replaySpectrogramImg" src="" alt="Pass Spectrogram">
                <div id="spectrogramCursor" class="spectrogram-cursor"></div>
              </div>
            </div>

            <!-- Timeline Scrubber & Speed Controls -->
            <div style="margin-bottom:12px;">
              <input type="range" id="replayScrubber" min="0" max="406" value="0" style="width:100%; accent-color:#38bdf8; cursor:pointer;" oninput="handleScrubberInput(this.value)">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-top:6px;">
                <div style="display:flex; gap:6px;">
                  <button id="btnPlayPause" class="btn-sm" style="background:#0284c7; color:white; font-weight:bold; padding:4px 12px;" onclick="togglePlayPause()">▶️ 再生</button>
                  <button class="btn-sm" onclick="restartPlayback()">⏮️ 最初から</button>
                </div>
                <div style="display:flex; gap:4px; align-items:center; font-size:0.8rem; color:#94a3b8;">
                  <span>速度:</span>
                  <button class="chip speed-chip active" onclick="setPlaySpeed(1, this)" style="padding:2px 8px; font-size:0.75rem;">1x</button>
                  <button class="chip speed-chip" onclick="setPlaySpeed(2, this)" style="padding:2px 8px; font-size:0.75rem;">2x</button>
                  <button class="chip speed-chip" onclick="setPlaySpeed(5, this)" style="padding:2px 8px; font-size:0.75rem;">5x</button>
                </div>
              </div>
            </div>

            <!-- GIF Clipper Section -->
            <div class="clipper-card">
              <div class="clipper-title">
                <span>🎥 GIF 切り出し ＆ ダウンロード</span>
              </div>
              <div class="clipper-controls">
                <div class="clipper-inputs-grid">
                  <div class="clipper-input-group">
                    <span>開始:</span>
                    <input type="number" id="trimStart" min="0" value="0">
                    <button class="btn-sm" onclick="setTrimStartToCurrent()">現位置</button>
                  </div>
                  <div class="clipper-input-group">
                    <span>終了:</span>
                    <input type="number" id="trimEnd" min="0" value="406">
                    <button class="btn-sm" onclick="setTrimEndToCurrent()">現位置</button>
                  </div>
                </div>
                <button class="btn-auto-window" onclick="autoSelectSkyWindowTrim()">🎯 開口窓区間を自動設定</button>
              </div>
              <button id="btnGenerateGif" class="btn-generate-gif" onclick="generateAndDownloadGif()">🎬 GIF を生成してダウンロード</button>
              <div id="gifProgressContainer" class="progress-bar-container">
                <div id="gifProgressBar" class="progress-bar-fill"></div>
              </div>
              <div id="gifProgressText" style="font-size:0.75rem; color:#94a3b8; text-align:center; margin-top:4px;"></div>
            </div>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="copy-btn" onclick="closeReplayModal()">閉じる</button>
      </div>
    </div>
  </div>

  <script src="/static/gifenc.js"></script>
  <script>
    function openImageModal(src) {
      document.getElementById('modalImg').src = src;
      document.getElementById('imageModal').classList.add('active');
    }
    function closeImageModal(e) {
      if (e.target.id === 'imageModal' || e.target.classList.contains('modal-close')) {
        document.getElementById('imageModal').classList.remove('active');
      }
    }

    async function openTextModal(filename, key) {
      const modal = document.getElementById('textModal');
      const title = document.getElementById('textModalTitle');
      const content = document.getElementById('textModalContent');
      title.textContent = filename;
      content.textContent = '読み込み中...';
      modal.classList.add('active');

      try {
        const res = await fetch('/content?key=' + encodeURIComponent(key));
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const text = await res.text();
        try {
          const parsed = JSON.parse(text);
          content.textContent = JSON.stringify(parsed, null, 2);
        } catch {
          content.textContent = text;
        }
      } catch (err) {
        content.textContent = 'エラー: ファイルを読み込めませんでした (' + err.message + ')';
      }
    }
    function closeTextModal() {
      document.getElementById('textModal').classList.remove('active');
    }
    function copyModalContent() {
      const text = document.getElementById('textModalContent').textContent;
      navigator.clipboard.writeText(text).then(() => {
        alert('クリップボードにコピーしました！');
      });
    }

    // --- バックアップ ＆ リストア モーダル ロジック ---
    function openBackupModal() {
      document.getElementById('backupModal').classList.add('active');
      loadBackupStats();
    }
    function closeBackupModal(e) {
      if (!e || e.target.id === 'backupModal' || e.target.classList.contains('modal-close') || e.target.classList.contains('copy-btn')) {
        document.getElementById('backupModal').classList.remove('active');
      }
    }
    async function loadBackupStats() {
      const loading = document.getElementById('backupStatsLoading');
      const content = document.getElementById('backupStatsContent');
      try {
        const res = await fetch('/api/backup/stats');
        const data = await res.json();
        document.getElementById('statTotalCount').textContent = data.total_count;
        document.getElementById('statTotalSize').textContent = (data.total_bytes / (1024*1024)).toFixed(1) + ' MB';
        document.getElementById('statResultsCount').textContent = data.results_count;
        document.getElementById('statArchiveCount').textContent = data.archive_count;
        document.getElementById('statGoldenCount').textContent = data.golden_count;
        document.getElementById('statRawCount').textContent = data.raw_count;
        loading.style.display = 'none';
        content.style.display = 'block';
      } catch (e) {
        loading.textContent = '統計取得エラー: ' + e;
      }
    }
    async function executeRestore() {
      const fileInput = document.getElementById('restoreFileInput');
      const statusDiv = document.getElementById('restoreStatus');
      const btn = document.getElementById('btnExecuteRestore');
      if (!fileInput.files || fileInput.files.length === 0) {
        alert('復元対象の .tar ファイルを選択してください。');
        return;
      }
      const file = fileInput.files[0];
      if (!confirm(`ファイル "${file.name}" (${(file.size / (1024*1024)).toFixed(1)} MB) から Garage S3 への復元を開始しますか？`)) {
        return;
      }
      btn.disabled = true;
      statusDiv.textContent = '復元中: S3 へオブジェクトをストリーミング展開しています...';
      try {
        const res = await fetch('/api/backup/restore', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-tar'
          },
          body: file
        });
        const data = await res.json();
        if (res.ok && data.status === 'ok') {
          statusDiv.style.color = '#10b981';
          statusDiv.textContent = `復元完了: ${data.restored_count} 件のオブジェクトを展開しました。`;
          loadBackupStats();
          loadPasses(1, true);
        } else {
          throw new Error(data.error || 'リストア失敗');
        }
      } catch (err) {
        statusDiv.style.color = '#ef4444';
        statusDiv.textContent = '復元エラー: ' + err.message;
      } finally {
        btn.disabled = false;
      }
    }

    // --- 今後の通過予測モーダル ロジック ---
    function closeFuturePassesModal(e) {
      if (!e || e.target.id === 'futurePassesModal' || e.target.classList.contains('modal-close') || e.target.classList.contains('copy-btn')) {
        document.getElementById('futurePassesModal').classList.remove('active');
      }
    }

    async function openFuturePassesModal() {
      const modal = document.getElementById('futurePassesModal');
      const listEl = document.getElementById('futurePassesList');
      listEl.innerHTML = '<div style="text-align:center; padding:20px; color:#94a3b8;">📅 向こう 24 時間のパスを SGP4 計算中...</div>';
      modal.classList.add('active');

      try {
        const res = await fetch('/api/future-passes');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();

        if (!data.passes || data.passes.length === 0) {
          listEl.innerHTML = '<div style="text-align:center; padding:20px; color:#94a3b8;">向こう 24 時間にスカイウィンドウ通過予測はありません。</div>';
          return;
        }

        let html = '';
        data.passes.forEach(p => {
          const aosJst = new Date(p.aos_time).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' });
          const tcaJst = new Date(p.tca_time).toLocaleTimeString('ja-JP', { timeZone: 'Asia/Tokyo' });
          const isExcellent = p.quality_score.includes('EXCELLENT');
          const isGood = p.quality_score.includes('GOOD');
          const isFair = p.quality_score.includes('FAIR');
          const badgeBg = isExcellent ? '#059669' : (isGood ? '#0284c7' : (isFair ? '#d97706' : '#475569'));

          const prob = p.success_probability !== undefined && p.success_probability !== null ? Math.round(p.success_probability * 100) : null;
          const probColor = prob !== null ? (prob >= 70 ? '#10b981' : (prob >= 40 ? '#38bdf8' : (prob >= 20 ? '#f59e0b' : '#94a3b8'))) : '#94a3b8';
          const probBar = prob !== null ? `
            <div style="margin-top:8px; padding-top:8px; border-top:1px dashed #334155; display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:0.8rem;">
              <span style="color:#94a3b8; white-space:nowrap;">🎯 復調期待度: <strong style="color:${probColor}; font-size:0.9rem;">${prob}%</strong></span>
              <div style="flex:1; background:#1e293b; border-radius:999px; height:6px; overflow:hidden;">
                <div style="background:${probColor}; width:${prob}%; height:100%; border-radius:999px;"></div>
              </div>
              <span style="color:#64748b; font-size:0.75rem; white-space:nowrap;">${p.history_summary || ''}</span>
            </div>
          ` : '';

          html += `
            <div style="background:rgba(255,255,255,0.03); border:1px solid #334155; border-radius:8px; padding:12px; margin-bottom:10px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                <span style="font-weight:bold; color:#38bdf8; font-size:1rem;">🛰️ ${p.satellite}</span>
                <span style="background:${badgeBg}; color:white; font-size:0.75rem; padding:2px 8px; border-radius:999px; font-weight:bold;">${p.quality_score}</span>
              </div>
              <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap:6px; font-size:0.85rem; color:#cbd5e1;">
                <div>AOS (出現): <strong style="color:white;">${aosJst}</strong></div>
                <div>TCA (最大仰角): <strong style="color:white;">${p.max_elevation}°</strong> (Az ${p.max_elevation_azimuth}°)</div>
                <div>開口窓滞在: <strong style="color:#38bdf8;">${p.window_duration_seconds} 秒</strong></div>
              </div>
              ${probBar}
            </div>
          `;
        });
        listEl.innerHTML = html;
      } catch (err) {
        listEl.innerHTML = `<div style="color:#ef4444; padding:10px;">予測データ取得エラー: ${err.message}</div>`;
      }
    }

    // --- 天球レーダー描画 ＆ 観測リプレイ ロジック ---
    let replayData = null;
    let replayCurrentSec = 0;
    let isPlaying = false;
    let playSpeed = 1;
    let playAnimationId = null;
    let lastPlayTimestamp = null;
    let replayDecodedImgElement = null;
    let showHeatmap = false;
    let skyHeatmapData = null;

    async function toggleSkyHeatmap() {
      showHeatmap = !showHeatmap;
      const btn = document.getElementById('btnToggleHeatmap');
      if (showHeatmap) {
        btn.textContent = '🎯 実績ヒートマップ: ON';
        btn.style.color = '#10b981';
        btn.style.borderColor = '#10b981';
        if (!skyHeatmapData) {
          try {
            const res = await fetch('/api/sky-mask/heatmap');
            if (res.ok) {
              skyHeatmapData = await res.json();
            }
          } catch (e) {
            console.error('Failed to load heatmap:', e);
          }
        }
      } else {
        btn.textContent = '🎯 実績ヒートマップ: OFF';
        btn.style.color = '#94a3b8';
        btn.style.borderColor = '#334155';
      }
      renderCurrentFrame();
    }

    function formatMinSec(sec) {
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    function closeReplayModal(e) {
      if (!e || e.target.id === 'replayModal' || e.target.classList.contains('modal-close') || e.target.classList.contains('copy-btn')) {
        pausePlayback();
        document.getElementById('replayModal').classList.remove('active');
      }
    }

    async function openReplayModal(passId) {
      const modal = document.getElementById('replayModal');
      document.getElementById('replayModalTitle').textContent = `🛰️ 天球観測リプレイ ＆ GIF 切り出し - ${passId}`;
      document.getElementById('repSatLabel').textContent = `🛰️ ${passId}`;
      modal.classList.add('active');

      // 状態リセット
      pausePlayback();
      replayCurrentSec = 0;
      replayData = null;
      document.getElementById('replayScrubber').value = 0;
      document.getElementById('gifProgressContainer').style.display = 'none';
      document.getElementById('gifProgressText').textContent = '';

      try {
        const res = await fetch(`/api/passes/${encodeURIComponent(passId)}/trajectory`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        replayData = await res.json();

        document.getElementById('replayModalTitle').textContent = `🛰️ 天球観測リプレイ ＆ GIF 切り出し - ${replayData.satellite}`;
        document.getElementById('repSatLabel').textContent = `🛰️ ${replayData.satellite} (${replayData.pass_id})`;
        const totalSec = replayData.total_seconds || (replayData.points.length - 1);
        const scrubber = document.getElementById('replayScrubber');
        scrubber.min = 0;
        scrubber.max = totalSec;
        scrubber.value = 0;
        document.getElementById('trimStart').value = 0;
        document.getElementById('trimEnd').value = totalSec;

        // スペクトログラム画像の読み込み
        const specImg = document.getElementById('replaySpectrogramImg');
        specImg.src = `/view?key=results/${encodeURIComponent(replayData.satellite)}/${encodeURIComponent(replayData.pass_id)}/spectrogram.png`;

        // 復調画像の確認
        const decodedBox = document.getElementById('replayDecodedBox');
        const decodedImg = document.getElementById('replayDecodedImg');
        if (replayData.images && replayData.images.length > 0) {
          decodedImg.src = replayData.images[0].url;
          replayDecodedImgElement = decodedImg;
        } else {
          decodedBox.style.display = 'none';
          replayDecodedImgElement = null;
        }

        renderCurrentFrame();
      } catch (err) {
        alert('軌道データの読み込みに失敗しました: ' + err.message);
        modal.classList.remove('active');
      }
    }

    function drawRadar(ctx, width, height, currentPoint, trajectoryPoints) {
      ctx.clearRect(0, 0, width, height);

      const xc = width / 2;
      const yc = height / 2;
      const margin = 26;
      const rMax = Math.min(xc, yc) - margin; // El = 0 deg

      const getRadius = (el) => Math.max(0, rMax * (1 - el / 90.0));
      const getXY = (az, el) => {
        const r = getRadius(el);
        const rad = az * Math.PI / 180.0;
        return {
          x: xc + r * Math.sin(rad),
          y: yc - r * Math.cos(rad)
        };
      };

      ctx.save();

      // 1. レーダー背景外円 (地平線 El=0)
      ctx.beginPath();
      ctx.arc(xc, yc, rMax, 0, 2 * Math.PI);
      ctx.fillStyle = '#080e1a';
      ctx.fill();
      ctx.strokeStyle = '#334155';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // 2. 南側建物躯体遮蔽マスク (方位 110°〜250°)
      // Canvas の円弧角度: 東が 0 度、北が -PI/2。
      // 方位角 Az からの変換: (Az - 90) * PI / 180
      const angle110 = (110 - 90) * Math.PI / 180.0;
      const angle250 = (250 - 90) * Math.PI / 180.0;
      ctx.beginPath();
      ctx.moveTo(xc, yc);
      ctx.arc(xc, yc, rMax, angle110, angle250, false);
      ctx.closePath();
      ctx.fillStyle = 'rgba(15, 23, 42, 0.78)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(239, 68, 68, 0.3)';
      ctx.stroke();

      // 3. 手すり遮蔽マスク (仰角 < 25°)
      const rEl25 = getRadius(25);
      ctx.beginPath();
      ctx.arc(xc, yc, rMax, 0, 2 * Math.PI, false);
      ctx.arc(xc, yc, rEl25, 0, 2 * Math.PI, true);
      ctx.fillStyle = 'rgba(30, 41, 59, 0.45)';
      ctx.fill();

      // 4. 上階庇遮蔽マスク (仰角 > 60°)
      const rEl60 = getRadius(60);
      ctx.beginPath();
      ctx.arc(xc, yc, rEl60, 0, 2 * Math.PI, false);
      ctx.fillStyle = 'rgba(30, 41, 59, 0.65)';
      ctx.fill();

      // 5. 北向きベランダ開口窓 (スカイウィンドウ): 方位 250°〜110°, 仰角 25°〜60°
      ctx.beginPath();
      ctx.arc(xc, yc, rEl25, angle250, angle110, false);
      ctx.arc(xc, yc, rEl60, angle110, angle250, true);
      ctx.closePath();
      ctx.fillStyle = 'rgba(6, 182, 212, 0.22)';
      ctx.fill();
      ctx.strokeStyle = '#06b6d4';
      ctx.lineWidth = 2;
      ctx.shadowColor = '#06b6d4';
      ctx.shadowBlur = 10;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // 5.5 復調実績ヒートマップ ＆ 経験的 Horizon Mask
      if (showHeatmap && skyHeatmapData && skyHeatmapData.cells && skyHeatmapData.cells.length > 0) {
        const maxCount = Math.max(1, ...skyHeatmapData.cells.map(c => c.count));
        skyHeatmapData.cells.forEach(c => {
          const rInner = getRadius(c.el + 5);
          const rOuter = getRadius(c.el);
          const aStart = (c.az - 90) * Math.PI / 180.0;
          const aEnd = (c.az + 10 - 90) * Math.PI / 180.0;
          const normDensity = Math.min(1.0, c.count / maxCount);
          const alpha = 0.15 + 0.50 * normDensity;

          ctx.beginPath();
          ctx.arc(xc, yc, rOuter, aStart, aEnd, false);
          ctx.arc(xc, yc, rInner, aEnd, aStart, true);
          ctx.closePath();
          ctx.fillStyle = `rgba(16, 185, 129, ${alpha.toFixed(2)})`;
          ctx.fill();
        });

        // 実測開口境界ポリゴン (経験的 Horizon Mask)
        if (skyHeatmapData.horizon_polygon && skyHeatmapData.horizon_polygon.length > 2) {
          ctx.beginPath();
          skyHeatmapData.horizon_polygon.forEach((pt, idx) => {
            const xy = getXY(pt.az + 5, pt.el_min);
            if (idx === 0) ctx.moveTo(xy.x, xy.y);
            else ctx.lineTo(xy.x, xy.y);
          });
          for (let i = skyHeatmapData.horizon_polygon.length - 1; i >= 0; i--) {
            const pt = skyHeatmapData.horizon_polygon[i];
            const xy = getXY(pt.az + 5, pt.el_max);
            ctx.lineTo(xy.x, xy.y);
          }
          ctx.closePath();
          ctx.strokeStyle = '#10b981';
          ctx.lineWidth = 1.5;
          ctx.setLineDash([4, 4]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }

      // 6. 仰角同心円グリッド (30°, 60°)
      [30, 60].forEach(el => {
        const r = getRadius(el);
        ctx.beginPath();
        ctx.arc(xc, yc, r, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.2)';
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(148, 163, 184, 0.6)';
        ctx.font = '10px monospace';
        ctx.fillText(`${el}°`, xc + 4, yc - r + 10);
      });

      // 7. 方位角放射線 (N, E, S, W)
      const cardinals = [
        { label: '北 (N 0°)', az: 0 },
        { label: '東 (E 90°)', az: 90 },
        { label: '南 (S 180°)', az: 180 },
        { label: '西 (W 270°)', az: 270 },
      ];
      cardinals.forEach(({ label, az }) => {
        const pt = getXY(az, 0);
        ctx.beginPath();
        ctx.moveTo(xc, yc);
        ctx.lineTo(pt.x, pt.y);
        ctx.strokeStyle = 'rgba(148, 163, 184, 0.25)';
        ctx.lineWidth = 1;
        ctx.stroke();

        const textPt = getXY(az, -7);
        ctx.fillStyle = az === 0 ? '#38bdf8' : (az === 180 ? '#94a3b8' : '#e2e8f0');
        ctx.font = 'bold 11px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, textPt.x, textPt.y);
      });

      // 開口窓ラベル
      const winCenterPt = getXY(0, 42.5);
      ctx.fillStyle = '#38bdf8';
      ctx.font = 'bold 11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('ベランダ開口窓', winCenterPt.x, winCenterPt.y - 6);
      ctx.font = '9px monospace';
      ctx.fillStyle = 'rgba(56, 189, 248, 0.85)';
      ctx.fillText('Az 250°~110° El 25°~60°', winCenterPt.x, winCenterPt.y + 7);

      // 遮蔽ラベル
      const southPt = getXY(180, 40);
      ctx.fillStyle = 'rgba(239, 68, 68, 0.7)';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('南側建物・躯体遮蔽', southPt.x, southPt.y);

      // 8. 観測軌道パス
      if (trajectoryPoints && trajectoryPoints.length > 1) {
        for (let i = 0; i < trajectoryPoints.length - 1; i++) {
          const p1 = trajectoryPoints[i];
          const p2 = trajectoryPoints[i + 1];
          const xy1 = getXY(p1.az, p1.el);
          const xy2 = getXY(p2.az, p2.el);

          ctx.beginPath();
          ctx.moveTo(xy1.x, xy1.y);
          ctx.lineTo(xy2.x, xy2.y);

          if (p1.in_window && p2.in_window) {
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 3;
            ctx.shadowColor = '#38bdf8';
            ctx.shadowBlur = 6;
          } else {
            ctx.strokeStyle = 'rgba(148, 163, 184, 0.35)';
            ctx.lineWidth = 1.5;
            ctx.shadowBlur = 0;
          }
          ctx.stroke();
          ctx.shadowBlur = 0;
        }
      }

      // 9. 衛星現在位置マーカー
      if (currentPoint) {
        const curXY = getXY(currentPoint.az, currentPoint.el);

        if (currentPoint.in_window) {
          const pulseR = 12 + (Date.now() / 100 % 10);
          ctx.beginPath();
          ctx.arc(curXY.x, curXY.y, pulseR, 0, 2 * Math.PI);
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.5)';
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }

        ctx.beginPath();
        ctx.arc(curXY.x, curXY.y, 6, 0, 2 * Math.PI);
        ctx.fillStyle = currentPoint.in_window ? '#34d399' : '#fbbf24';
        ctx.shadowColor = currentPoint.in_window ? '#34d399' : '#fbbf24';
        ctx.shadowBlur = 10;
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.font = '14px sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText('🛰️', curXY.x + 8, curXY.y - 8);

        ctx.font = '10px monospace';
        ctx.fillStyle = '#f8fafc';
        ctx.fillText(`Az ${currentPoint.az}° / El ${currentPoint.el}°`, curXY.x + 28, curXY.y - 8);
      }

      ctx.restore();
    }

    function renderCurrentFrame() {
      if (!replayData || !replayData.points || replayData.points.length === 0) return;
      const totalSec = replayData.total_seconds || (replayData.points.length - 1);
      const secIdx = Math.max(0, Math.min(Math.floor(replayCurrentSec), replayData.points.length - 1));
      const pt = replayData.points[secIdx];

      // レーダー描画
      const canvas = document.getElementById('radarCanvas');
      if (canvas) {
        const ctx = canvas.getContext('2d');
        drawRadar(ctx, canvas.width, canvas.height, pt, replayData.points);
      }

      // スペクトログラムカーソル同期
      const cursor = document.getElementById('spectrogramCursor');
      if (cursor) {
        const pct = totalSec > 0 ? (replayCurrentSec / totalSec) * 100 : 0;
        cursor.style.left = `${pct}%`;
      }

      // テレメトリ数値更新
      const curMinSec = formatMinSec(Math.floor(replayCurrentSec));
      const totalMinSec = formatMinSec(totalSec);
      document.getElementById('repCurrentTimeText').textContent = `${curMinSec} / ${totalMinSec} (t=${Math.floor(replayCurrentSec)}s)`;

      if (pt.timestamp_utc) {
        const jstStr = new Date(pt.timestamp_utc).toLocaleTimeString('ja-JP', { timeZone: 'Asia/Tokyo' });
        document.getElementById('repTimeJst').textContent = jstStr;
      } else {
        document.getElementById('repTimeJst').textContent = `t=${pt.t}s`;
      }

      document.getElementById('repAz').textContent = `${pt.az}°`;
      document.getElementById('repEl').textContent = `${pt.el}°`;
      document.getElementById('repRange').textContent = `${pt.range_km} km`;

      // スカイウィンドウ突入バッジ
      const badge = document.getElementById('windowStatusBadge');
      if (pt.in_window) {
        badge.textContent = '✅ スカイウィンドウ突入中 (開口窓)';
        badge.style.background = '#065f46';
        badge.style.color = '#34d399';
        badge.style.borderColor = '#10b981';
      } else {
        badge.textContent = '⛔ 建物・手すり遮蔽';
        badge.style.background = '#1e293b';
        badge.style.color = '#94a3b8';
        badge.style.borderColor = '#334155';
      }

      // スクラバーバー同期
      const scrubber = document.getElementById('replayScrubber');
      if (scrubber && Math.abs(scrubber.value - replayCurrentSec) > 1) {
        scrubber.value = Math.floor(replayCurrentSec);
      }

      // 復調画像ポップアップ
      const decodedBox = document.getElementById('replayDecodedBox');
      if (decodedBox && replayDecodedImgElement) {
        if (pt.has_image) {
          decodedBox.style.display = 'block';
          decodedBox.style.borderColor = '#38bdf8';
          decodedBox.style.boxShadow = '0 0 12px rgba(56, 189, 248, 0.4)';
        } else {
          decodedBox.style.display = 'none';
          decodedBox.style.boxShadow = 'none';
        }
      }
    }

    function togglePlayPause() {
      if (isPlaying) {
        pausePlayback();
      } else {
        startPlayback();
      }
    }

    function startPlayback() {
      if (!replayData || !replayData.points) return;
      const totalSec = replayData.total_seconds || (replayData.points.length - 1);
      if (replayCurrentSec >= totalSec) {
        replayCurrentSec = 0;
      }
      isPlaying = true;
      document.getElementById('btnPlayPause').textContent = '⏸️ 一時停止';
      lastPlayTimestamp = performance.now();
      playAnimationId = requestAnimationFrame(playStep);
    }

    function pausePlayback() {
      isPlaying = false;
      if (playAnimationId) {
        cancelAnimationFrame(playAnimationId);
        playAnimationId = null;
      }
      const btn = document.getElementById('btnPlayPause');
      if (btn) btn.textContent = '▶️ 再生';
    }

    function restartPlayback() {
      replayCurrentSec = 0;
      renderCurrentFrame();
      if (!isPlaying) startPlayback();
    }

    function setPlaySpeed(speed, btnEl) {
      playSpeed = speed;
      document.querySelectorAll('.speed-chip').forEach(el => el.classList.remove('active'));
      if (btnEl) btnEl.classList.add('active');
    }

    function playStep(now) {
      if (!isPlaying || !replayData || !replayData.points) return;
      const deltaSec = (now - lastPlayTimestamp) / 1000.0;
      lastPlayTimestamp = now;

      const totalSec = replayData.total_seconds || (replayData.points.length - 1);
      replayCurrentSec += deltaSec * playSpeed;

      if (replayCurrentSec >= totalSec) {
        replayCurrentSec = totalSec;
        renderCurrentFrame();
        pausePlayback();
        return;
      }

      renderCurrentFrame();
      playAnimationId = requestAnimationFrame(playStep);
    }

    function handleScrubberInput(val) {
      replayCurrentSec = parseInt(val, 10);
      renderCurrentFrame();
    }

    function handleSpectrogramClick(e) {
      if (!replayData || !replayData.points) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const pct = Math.max(0, Math.min(1, clickX / rect.width));
      const totalSec = replayData.total_seconds || (replayData.points.length - 1);
      replayCurrentSec = Math.round(pct * totalSec);
      document.getElementById('replayScrubber').value = replayCurrentSec;
      renderCurrentFrame();
    }

    function setTrimStartToCurrent() {
      document.getElementById('trimStart').value = Math.floor(replayCurrentSec);
    }

    function setTrimEndToCurrent() {
      document.getElementById('trimEnd').value = Math.floor(replayCurrentSec);
    }

    function autoSelectSkyWindowTrim() {
      if (!replayData || !replayData.points) return;
      const inWindowPoints = replayData.points.filter(p => p.in_window);
      if (inWindowPoints.length === 0) {
        alert('このパスにはスカイウィンドウ通過区間がありません。');
        return;
      }
      const firstT = inWindowPoints[0].t;
      const lastT = inWindowPoints[inWindowPoints.length - 1].t;
      document.getElementById('trimStart').value = firstT;
      document.getElementById('trimEnd').value = lastT;
      replayCurrentSec = firstT;
      renderCurrentFrame();
    }

    async function generateAndDownloadGif() {
      if (!replayData || !replayData.points) return;
      const gifencModule = window.gifenc || (typeof gifenc !== 'undefined' ? gifenc : null);
      if (!gifencModule) {
        alert('GIF エンコーダ (gifenc) が読み込まれていません。');
        return;
      }

      const startSec = parseInt(document.getElementById('trimStart').value, 10) || 0;
      const endSec = parseInt(document.getElementById('trimEnd').value, 10) || (replayData.points.length - 1);
      const clampedStart = Math.max(0, Math.min(startSec, replayData.points.length - 1));
      const clampedEnd = Math.max(clampedStart + 1, Math.min(endSec, replayData.points.length - 1));

      const btn = document.getElementById('btnGenerateGif');
      const progressBox = document.getElementById('gifProgressContainer');
      const progressBar = document.getElementById('gifProgressBar');
      const progressText = document.getElementById('gifProgressText');

      btn.disabled = true;
      progressBox.style.display = 'block';
      progressBar.style.width = '0%';
      progressText.textContent = 'GIF 初期化中...';

      try {
        const { GIFEncoder, quantize, applyPalette } = gifencModule;
        const gif = GIFEncoder();

        const gifCanvas = document.createElement('canvas');
        const gw = 440;
        const gh = 440;
        gifCanvas.width = gw;
        gifCanvas.height = gh;
        const gctx = gifCanvas.getContext('2d');

        const totalFrames = clampedEnd - clampedStart + 1;
        let step = 1;
        if (totalFrames > 120) step = 2; // 出力サイズ抑制

        let frameCount = 0;
        const expectedFrames = Math.ceil(totalFrames / step);

        for (let sec = clampedStart; sec <= clampedEnd; sec += step) {
          const pt = replayData.points[sec];
          drawRadar(gctx, gw, gh, pt, replayData.points);

          // 上部情報ヘッダー
          gctx.save();
          gctx.fillStyle = 'rgba(15, 23, 42, 0.88)';
          gctx.fillRect(8, 8, gw - 16, 32);
          gctx.strokeStyle = '#334155';
          gctx.strokeRect(8, 8, gw - 16, 32);

          gctx.fillStyle = '#38bdf8';
          gctx.font = 'bold 11px sans-serif';
          gctx.textAlign = 'left';
          gctx.fillText(`🛰️ ${replayData.satellite} (t=${pt.t}s)`, 16, 28);

          gctx.fillStyle = pt.in_window ? '#34d399' : '#fbbf24';
          gctx.textAlign = 'right';
          gctx.fillText(pt.in_window ? '✅ 開口窓突入' : '⛔ 建物遮蔽', gw - 16, 28);

          // 下部テレメトリバー
          gctx.fillStyle = 'rgba(15, 23, 42, 0.88)';
          gctx.fillRect(8, gh - 40, gw - 16, 32);
          gctx.strokeStyle = '#334155';
          gctx.strokeRect(8, gh - 40, gw - 16, 32);

          gctx.fillStyle = '#f8fafc';
          gctx.font = '11px monospace';
          gctx.textAlign = 'left';
          gctx.fillText(`Az: ${pt.az}° | El: ${pt.el}° | Range: ${pt.range_km}km`, 16, gh - 20);

          // 復調画像 PiP サムネイル
          if (replayDecodedImgElement && replayDecodedImgElement.complete && replayDecodedImgElement.naturalWidth > 0 && pt.has_image) {
            const thumbW = 64;
            const thumbH = 64;
            const tx = gw - thumbW - 12;
            const ty = gh - thumbH - 46;
            gctx.strokeStyle = '#38bdf8';
            gctx.lineWidth = 1.5;
            gctx.strokeRect(tx, ty, thumbW, thumbH);
            gctx.drawImage(replayDecodedImgElement, tx, ty, thumbW, thumbH);
          }

          gctx.restore();

          const imgData = gctx.getImageData(0, 0, gw, gh).data;
          const palette = quantize(imgData, 256);
          const index = applyPalette(imgData, palette);
          gif.writeFrame(index, gw, gh, { palette, delay: step * 100 });

          frameCount++;
          const pct = Math.min(100, Math.round((frameCount / expectedFrames) * 100));
          progressBar.style.width = pct + '%';
          progressText.textContent = `${pct}% (${frameCount} / ${expectedFrames} フレーム処理中...)`;

          await new Promise(r => setTimeout(r, 0));
        }

        gif.finish();
        const bytes = gif.bytes();
        const blob = new Blob([bytes], { type: 'image/gif' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${replayData.pass_id}_balcony_replay.gif`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        progressText.textContent = `✅ GIF 保存完了! (${Math.round(bytes.length / 1024)} KB)`;
      } catch (err) {
        console.error('GIF export error:', err);
        alert('GIF 生成中にエラーが発生しました: ' + err.message);
      } finally {
        btn.disabled = false;
      }
    }

    // --- 無限スクロール & 動的ロード ロジック ---
    let currentPage = 1;
    let currentCategory = 'all';
    let isLoading = false;
    let hasMore = true;

    async function loadPasses(page = 1, reset = false) {
      if (isLoading) return;
      if (!reset && !hasMore) return;
      isLoading = true;

      const loader = document.getElementById('scrollLoader');
      if (loader) loader.classList.add('active');

      const toggle = document.getElementById('hasPacketsToggle');
      const hasPacketsOnly = toggle ? toggle.checked : false;

      const url = `/api/passes?page=${page}&limit=12&category=${encodeURIComponent(currentCategory)}&has_data=${hasPacketsOnly ? 'true' : 'false'}`;
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();

        const container = document.getElementById('cardsContainer');
        if (reset) {
          container.innerHTML = data.cards_html;
          currentPage = 1;
        } else {
          container.insertAdjacentHTML('beforeend', data.cards_html);
          currentPage = page;
        }

        hasMore = data.has_more;

        if (data.counts) {
          if (document.getElementById('count-all')) document.getElementById('count-all').textContent = data.counts.all || 0;
          if (document.getElementById('count-spacestation')) document.getElementById('count-spacestation').textContent = data.counts.spacestation || 0;
          if (document.getElementById('count-weathersatellite')) document.getElementById('count-weathersatellite').textContent = data.counts.weathersatellite || 0;
          if (document.getElementById('count-cubesat')) document.getElementById('count-cubesat').textContent = data.counts.cubesat || 0;
        }

        const matchedEl = document.getElementById('matchedCount');
        if (matchedEl) {
          const renderedCount = container.querySelectorAll('.card').length;
          matchedEl.textContent = `表示中: ${renderedCount} / ${data.total_matched} 件 (全 ${data.counts ? data.counts.all : 0} 件)`;
        }

        const noMatchEl = document.getElementById('noMatchState');
        if (noMatchEl) {
          noMatchEl.style.display = (data.total_matched === 0) ? 'block' : 'none';
        }
      } catch (err) {
        console.error('Error loading passes:', err);
      } finally {
        isLoading = false;
        if (loader) loader.classList.remove('active');
      }
    }

    function setCategoryFilter(category, btnElement) {
      currentCategory = category;
      document.querySelectorAll('.filter-chips .chip').forEach(el => el.classList.remove('active'));
      if (btnElement) btnElement.classList.add('active');
      loadPasses(1, true);
    }

    function applyFilters() {
      loadPasses(1, true);
    }

    function setupInfiniteScroll() {
      const sentinel = document.getElementById('scrollSentinel');
      if (!sentinel) return;

      const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting && !isLoading && hasMore) {
            loadPasses(currentPage + 1, false);
          }
        });
      }, { rootMargin: '400px' });

      observer.observe(sentinel);
    }

    window.addEventListener('DOMContentLoaded', () => {
      loadPasses(1, true);
      setupInfiniteScroll();
    });
  </script>
</body>
</html>
"""


def parse_pass_id(pass_id: str):
    """
    pass_id から衛星名と開始日時 (UTC) を抽出。
    例: 'METEOR-M2 3_20261025_005344' -> ('METEOR-M2 3', datetime(2026, 10, 25, 0, 53, 44, tzinfo=timezone.utc))
    例: '20261025_005344' -> (None, datetime(2026, 10, 25, 0, 53, 44, tzinfo=timezone.utc))
    """
    match = re.search(r"(\d{8}_\d{6})", pass_id)
    if not match:
        return None, None

    dt_str = match.group(1)
    try:
        dt = datetime.strptime(dt_str, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None, None

    prefix = pass_id[:match.start()].rstrip("_")
    sat_name = prefix if prefix else None
    return sat_name, dt


def generate_trajectory_data(pass_id: str, cache) -> dict:
    """
    指定された pass_id の軌道時系列データ (Az, El, Range, in_window, has_image) と
    復調画像一覧を生成。
    """
    pdata = cache._passes.get(pass_id) or {}
    satellite = pdata.get("satellite")
    parsed_sat, parsed_dt = parse_pass_id(pass_id)

    if not satellite:
        satellite = parsed_sat

    if not satellite:
        return None

    start_time = parsed_dt
    if not start_time:
        start_time = pdata.get("last_modified_dt") or datetime.now(timezone.utc)

    summary = pdata.get("summary") or {}
    duration = summary.get("duration_seconds", 406.0)
    try:
        duration_sec = int(float(duration))
    except (ValueError, TypeError):
        duration_sec = 406

    tle = load_tle_for_satellite(satellite)
    if not tle:
        return None

    calc = OrbitCalculator()
    points = calc.calculate_trajectory(
        tle_line1=tle[0],
        tle_line2=tle[1],
        start_time=start_time,
        duration_seconds=duration_sec,
        step_seconds=1
    )

    # 復調画像の抽出
    files = pdata.get("files") or {}
    images = []
    for fname, key in files.items():
        if fname.lower().endswith(".png") and "spectrogram" not in fname.lower():
            images.append({
                "name": os.path.basename(fname),
                "url": f"/view?key={urllib.parse.quote(key, safe='/:')}"
            })

    # packets 内の画像リストもフォールバック確認
    if not images and pdata.get("packets"):
        pkts = pdata.get("packets", {}).get("packets", [])
        for pkt in pkts:
            for img_name in pkt.get("images", []):
                images.append({
                    "name": img_name,
                    "url": f"/view?key=results/{urllib.parse.quote(satellite)}/{urllib.parse.quote(pass_id)}/{urllib.parse.quote(img_name)}"
                })

    has_decoded_images = len(images) > 0
    # 画像が存在する場合、スカイウィンドウ内かつピーク付近のポイントに has_image = True を付与
    point_dicts = []
    for pt in points:
        pt_dict = asdict(pt)
        if has_decoded_images and pt.in_window:
            pt_dict["has_image"] = True
        point_dicts.append(pt_dict)

    return {
        "pass_id": pass_id,
        "satellite": satellite,
        "total_seconds": duration_sec,
        "points": point_dicts,
        "images": images,
    }


def generate_future_passes_data(now_dt=None, horizon_hours: int = 24, cache=None) -> dict:
    """
    今後 horizon_hours 時間における北向きベランダスカイウィンドウ通過予測リストを生成。
    直近の復調実績統計（cache）を反映して成功確率・期待スコアを算出。
    """
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    if cache is None:
        try:
            cache = pass_cache
        except Exception:
            cache = None

    history_stats = cache.get_satellite_history_stats() if cache else None

    targets = []
    for sat_name in DEFAULT_TARGET_TLES.keys():
        tle = load_tle_for_satellite(sat_name)
        if tle:
            targets.append((sat_name, tle[0], tle[1]))

    calc = OrbitCalculator()
    future_passes = calc.find_future_passes(
        targets=targets,
        start_time=now_dt,
        horizon_hours=horizon_hours,
        step_seconds=30,
        history_stats=history_stats,
    )

    return {
        "generated_at": now_dt.isoformat(),
        "passes": [asdict(p) for p in future_passes]
    }


def generate_s3_backup_stream(s3_client, bucket: str, prefixes=("results/", "archive/", "golden/", "raw/")):
    """
    Garage S3 の対象プレフィックス配下にある全オブジェクトを、
    ディスク一時ファイルを作らず 512 バイトブロック単位の Tar ストリームとして順次イールドする。
    """
    paginator = s3_client.get_paginator("list_objects_v2")
    seen_keys = set()

    for prefix in prefixes:
        try:
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
            for page in pages:
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if key in seen_keys or key.endswith("/"):
                        continue
                    seen_keys.add(key)
                    size = item["Size"]
                    mtime = item.get("LastModified")
                    mtime_ts = int(mtime.timestamp()) if hasattr(mtime, "timestamp") else int(time.time())

                    ti = tarfile.TarInfo(name=key)
                    ti.size = size
                    ti.mtime = mtime_ts
                    ti.mode = 0o644
                    yield ti.tobuf()

                    if size > 0:
                        obj = s3_client.get_object(Bucket=bucket, Key=key)
                        body = obj["Body"]
                        while chunk := body.read(65536):
                            yield chunk

                        pad = (512 - (size % 512)) % 512
                        if pad > 0:
                            yield b"\x00" * pad
        except Exception as e:
            print(f"Error streaming prefix {prefix} in backup: {e}", flush=True)

    yield b"\x00" * 1024


def get_s3_backup_stats(s3_client, bucket: str) -> dict:
    paginator = s3_client.get_paginator("list_objects_v2")
    stats = {
        "status": "ok",
        "total_count": 0,
        "total_bytes": 0,
        "results_count": 0,
        "archive_count": 0,
        "golden_count": 0,
        "raw_count": 0,
    }
    for prefix in ("results/", "archive/", "golden/", "raw/"):
        try:
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
            for page in pages:
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if key.endswith("/"):
                        continue
                    stats["total_count"] += 1
                    stats["total_bytes"] += item["Size"]
                    if key.startswith("results/"):
                        stats["results_count"] += 1
                    elif key.startswith("archive/"):
                        stats["archive_count"] += 1
                    elif key.startswith("golden/"):
                        stats["golden_count"] += 1
                    elif key.startswith("raw/"):
                        stats["raw_count"] += 1
        except Exception as e:
            print(f"Error counting stats for {prefix}: {e}", flush=True)
    return stats


def get_satellite_type(p: dict) -> str:
    summary = p.get("summary") or {}
    sat_type = summary.get("satellite_type")
    if sat_type:
        return sat_type

    sat_upper = (p.get("satellite") or "").upper()
    if "ISS" in sat_upper:
        return "SpaceStation"
    elif "METEOR" in sat_upper:
        return "WeatherSatellite"
    elif "FUNCUBE" in sat_upper or "AO-73" in sat_upper:
        return "CubeSat"
    return "GenericSatellite"


def resolve_card_metadata(p: dict):
    summary = p.get("summary") or {}
    sat = p.get("satellite", "")
    sat_upper = sat.upper()

    sat_type = get_satellite_type(p)
    signal_type = summary.get("signal_type")
    freq_label = summary.get("frequency_label")
    icon = summary.get("display_icon")
    summary_text = summary.get("summary_text", "")

    if not signal_type or not freq_label or not icon:
        if sat_type == "SpaceStation":
            signal_type = signal_type or "APRS / AX.25 (1200bps AFSK)"
            freq_label = freq_label or "145.825 MHz"
            icon = icon or "🚀"
        elif sat_type == "WeatherSatellite":
            signal_type = signal_type or "LRPT (QPSK 72kbps)"
            freq_label = freq_label or "137.900 MHz"
            icon = icon or "🛰️"
        elif sat_type == "CubeSat":
            signal_type = signal_type or "BPSK (1200bps Telemetry)"
            freq_label = freq_label or "145.935 MHz"
            icon = icon or "📻"
        else:
            signal_type = signal_type or "Audio / RF Spectrum"
            freq_label = freq_label or "-"
            icon = icon or "📡"

    return sat_type, signal_type, freq_label, icon, summary_text


def render_card_html(p: dict) -> str:
    spectrogram_html = ""
    if "spectrogram.png" in p.get("files", {}):
        img_url = f"/view?key={urllib.parse.quote(p['files']['spectrogram.png'])}"
        spectrogram_html = f"""
        <div class="spectrogram-container">
          <img class="spectrogram-img" loading="lazy" src="{img_url}" alt="Spectrogram" onclick="openImageModal('{img_url}')">
          <span class="spectrogram-hint">🔍 スペクトログラム拡大</span>
        </div>
        """

    decoded_images_html = ""
    for fname, fkey in p.get("files", {}).items():
        if fname != "spectrogram.png" and any(fname.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg")):
            decoded_img_url = f"/view?key={urllib.parse.quote(fkey)}"
            decoded_images_html += f"""
            <div class="spectrogram-container" style="margin-top: 8px;">
              <div style="padding: 4px 8px; font-size: 0.75rem; background: rgba(56, 189, 248, 0.2); color: var(--accent); font-weight: bold; text-align: left;">
                🛰️ 復調気象画像: {fname}
              </div>
              <img class="spectrogram-img" loading="lazy" src="{decoded_img_url}" alt="{fname}" onclick="openImageModal('{decoded_img_url}')">
              <span class="spectrogram-hint">🔍 気象画像タップで拡大</span>
            </div>
            """

    summary = p.get("summary") or {}
    status = summary.get("status", "completed")
    packets_count = summary.get("packets_count", 0)
    sat = p.get("satellite", "")
    sat_upper = sat.upper()

    sat_type, signal_type, freq_label, icon, summary_text = resolve_card_metadata(p)

    summary_box_html = ""
    if summary_text:
        summary_box_html = f"""
        <div class="summary-box">
          <span>{icon}</span>
          <span>{summary_text}</span>
        </div>
        """

    summary_accordion = ""
    if summary:
        pretty_summary = json.dumps(summary, indent=2, ensure_ascii=False)
        summary_accordion = f"""
        <details class="data-accordion">
          <summary>📊 解析サマリ (summary.json) の中身を見る</summary>
          <div class="accordion-content">
            <pre class="code-block"><code>{pretty_summary}</code></pre>
          </div>
        </details>
        """

    packets_accordion = ""
    packets_data = p.get("packets") or {}
    packet_items = packets_data.get("packets", [])
    if packet_items:
        pretty_packets = "\n\n".join([json.dumps(item, indent=2, ensure_ascii=False) for item in packet_items])
        accordion_title = f"📡 受信パケット ({len(packet_items)} 件) の中身を見る"
        if "FUNCUBE" in sat_upper or "AO-73" in sat_upper:
            accordion_title = f"📻 受信テレメトリ ({len(packet_items)} 件) の中身を見る"
        packets_accordion = f"""
        <details class="data-accordion">
          <summary>{accordion_title}</summary>
          <div class="accordion-content">
            <pre class="code-block"><code>{pretty_packets}</code></pre>
          </div>
        </details>
        """
    elif "ISS" in sat_upper:
        packets_accordion = f"""
        <details class="data-accordion">
          <summary>📡 APRS 受信パケット (0 件)</summary>
          <div class="accordion-content">
            <pre class="code-block"><code>パケットは検出されませんでした (0 packets)</code></pre>
          </div>
        </details>
        """
    elif "FUNCUBE" in sat_upper or "AO-73" in sat_upper:
        packets_accordion = f"""
        <details class="data-accordion">
          <summary>📻 BPSK テレメトリフレーム (0 件)</summary>
          <div class="accordion-content">
            <pre class="code-block"><code>テレメトリフレームは検出されませんでした (0 frames)</code></pre>
          </div>
        </details>
        """

    file_rows = []
    for fname, fkey in p.get("files", {}).items():
        dl_url = f"/download?key={urllib.parse.quote(fkey)}"

        if fname.endswith(".png"):
            view_btn = f"""<button class="action-btn btn-view" onclick="openImageModal('/view?key={urllib.parse.quote(fkey)}')">👁️ 表示</button>"""
        elif fname.endswith(".json") or fname.endswith(".txt"):
            view_btn = f"""<button class="action-btn btn-view" onclick="openTextModal('{fname}', '{fkey}')">👁️ 中身を見る</button>"""
        else:
            view_btn = ""

        file_row = f"""
        <div class="file-row">
          <span class="file-name">📄 {fname}</span>
          <div class="file-actions">
            {view_btn}
            <a class="action-btn btn-download" href="{dl_url}">📥 保存</a>
          </div>
        </div>
        """
        file_rows.append(file_row)

    files_grid_html = "".join(file_rows)
    badge_class = f"badge-{sat_type.lower()}"
    has_data_val = "true" if (packets_count > 0 or sat_type in ["WeatherSatellite", "CubeSat"]) else "false"

    return f"""
    <div class="card" data-satellite-type="{sat_type}" data-satellite-name="{sat}" data-packets-count="{packets_count}" data-has-data="{has_data_val}">
      <div class="card-header">
        <div class="sat-header-left">
          <div class="sat-name">{icon} {p.get('satellite', '')}</div>
          <div class="sat-badges">
            <span class="badge-tag {badge_class}">{sat_type}</span>
            <span class="badge-tag badge-freq">{freq_label}</span>
            <span class="badge-tag badge-signal">{signal_type}</span>
          </div>
          <div class="pass-id">{p.get('pass_id', '')}</div>
        </div>
        <div class="sat-header-right">
          <button class="btn-sm btn-replay" onclick="openReplayModal('{p.get('pass_id', '')}')">🛰️ 天球リプレイ</button>
          <span class="badge-status">{status}</span>
        </div>
      </div>
      <div class="card-body">
        {spectrogram_html}
        {decoded_images_html}
        {summary_box_html}
        <div class="meta-grid">
          <div class="meta-item">
            <span class="meta-label">観測・処理時刻</span>
            <span class="meta-val">{p.get('last_modified', '')}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">受信信号 / パケット</span>
            <span class="meta-val">{packets_count} 件検出</span>
          </div>
        </div>
        {summary_accordion}
        {packets_accordion}
        <div class="files-section">
          <div class="files-title">ファイル一覧 (中身プレビュー ＆ ダウンロード)</div>
          <div class="files-grid">
            {files_grid_html}
          </div>
        </div>
      </div>
    </div>
    """


class PassDataCache:
    def __init__(self, s3_client, s3_bucket: str, ttl_seconds: float = 10.0):
        self.s3_client = s3_client
        self.s3_bucket = s3_bucket
        self.ttl_seconds = ttl_seconds
        self.last_sync_time = 0.0
        self._passes = {}
        self._file_mtimes = {}
        self._heatmap_cache = None
        self._lock = threading.Lock()

    def get_all_passes(self):
        now = time.time()
        if self._passes and (now - self.last_sync_time < self.ttl_seconds):
            return sorted(
                self._passes.values(),
                key=lambda x: x.get("last_modified_dt", datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True
            )

        with self._lock:
            now = time.time()
            if self._passes and (now - self.last_sync_time < self.ttl_seconds):
                return sorted(
                    self._passes.values(),
                    key=lambda x: x.get("last_modified_dt", datetime.min.replace(tzinfo=timezone.utc)),
                    reverse=True
                )

            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.s3_bucket, Prefix="results/")

            active_pass_ids = set()
            new_file_mtimes = {}

            for page in pages:
                for item in page.get("Contents", []):
                    key = item["Key"]
                    mtime = item["LastModified"]
                    new_file_mtimes[key] = mtime

                    parts = key.split("/")
                    if len(parts) >= 4:
                        sat = parts[1]
                        pass_id = parts[2]
                        filename = "/".join(parts[3:])
                        active_pass_ids.add(pass_id)

                        if pass_id not in self._passes:
                            self._passes[pass_id] = {
                                "satellite": sat,
                                "pass_id": pass_id,
                                "last_modified": mtime.strftime("%Y-%m-%d %H:%M:%S UTC"),
                                "last_modified_dt": mtime,
                                "files": {},
                                "summary": None,
                                "packets": None,
                            }
                        else:
                            if mtime > self._passes[pass_id]["last_modified_dt"]:
                                self._passes[pass_id]["last_modified_dt"] = mtime
                                self._passes[pass_id]["last_modified"] = mtime.strftime("%Y-%m-%d %H:%M:%S UTC")

                        self._passes[pass_id]["files"][filename] = key

            for pass_id in active_pass_ids:
                pdata = self._passes[pass_id]
                if "summary.json" in pdata["files"]:
                    skey = pdata["files"]["summary.json"]
                    current_mtime = new_file_mtimes.get(skey)
                    if pdata["summary"] is None or self._file_mtimes.get(skey) != current_mtime:
                        try:
                            s_obj = self.s3_client.get_object(Bucket=self.s3_bucket, Key=skey)
                            pdata["summary"] = json.loads(s_obj["Body"].read().decode("utf-8"))
                        except Exception:
                            pass

                if "packets.json" in pdata["files"]:
                    pkey = pdata["files"]["packets.json"]
                    current_mtime = new_file_mtimes.get(pkey)
                    if pdata["packets"] is None or self._file_mtimes.get(pkey) != current_mtime:
                        try:
                            pk_obj = self.s3_client.get_object(Bucket=self.s3_bucket, Key=pkey)
                            pdata["packets"] = json.loads(pk_obj["Body"].read().decode("utf-8"))
                        except Exception:
                            pass

            self._file_mtimes = new_file_mtimes
            for stale_id in list(self._passes.keys()):
                if stale_id not in active_pass_ids:
                    del self._passes[stale_id]

            self.last_sync_time = time.time()

            return sorted(
                self._passes.values(),
                key=lambda x: x.get("last_modified_dt", datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True
            )

    def get_satellite_history_stats(self) -> dict:
        """
        キャッシュされた全パス情報から、衛星別・仰角帯別の復調成功実績を集計。
        """
        if not self._passes and self.s3_client:
            try:
                self.get_all_passes()
            except Exception:
                pass

        stats = {}
        for pass_id, pdata in self._passes.items():
            pid = pdata.get("pass_id") or pass_id
            sat = pdata.get("satellite")
            if not sat:
                sat, _ = parse_pass_id(pid)
            if not sat:
                continue

            if sat not in stats:
                stats[sat] = {
                    "total_passes": 0,
                    "successful_passes": 0,
                    "overall_success_rate": 0.0,
                    "elevation_bands": {
                        "high": {"total": 0, "success": 0, "rate": 0.0},
                        "mid": {"total": 0, "success": 0, "rate": 0.0},
                        "low": {"total": 0, "success": 0, "rate": 0.0},
                    }
                }

            # 復調成功判定: packets_count > 0 または画像ファイルが存在
            summary = pdata.get("summary") or {}
            packets_count = summary.get("packets_count", 0)
            files = pdata.get("files") or {}
            has_images = any(f.lower().endswith(".png") and "spectrogram" not in f.lower() for f in files)
            is_success = (packets_count > 0) or has_images

            # 最大仰角の判定
            max_el = pdata.get("max_elevation")
            if max_el is None:
                max_el = summary.get("max_elevation")
            if max_el is None:
                parsed_sat, parsed_dt = parse_pass_id(pass_id)
                tle = load_tle_for_satellite(sat)
                if parsed_dt and tle:
                    try:
                        dur = int(float(summary.get("duration_seconds", 406.0)))
                        calc = OrbitCalculator()
                        pts = calc.calculate_trajectory(tle[0], tle[1], parsed_dt, dur, step_seconds=15)
                        if pts:
                            max_el = max(p.el for p in pts)
                            pdata["max_elevation"] = max_el
                    except Exception:
                        pass

            if max_el is None:
                max_el = 25.0

            if max_el >= 35.0:
                band = "high"
            elif max_el >= 20.0:
                band = "mid"
            else:
                band = "low"

            s_entry = stats[sat]
            s_entry["total_passes"] += 1
            if is_success:
                s_entry["successful_passes"] += 1

            b_entry = s_entry["elevation_bands"][band]
            b_entry["total"] += 1
            if is_success:
                b_entry["success"] += 1

        for sat, s_entry in stats.items():
            tot = s_entry["total_passes"]
            succ = s_entry["successful_passes"]
            s_entry["overall_success_rate"] = (succ / tot) if tot > 0 else 0.0
            for b_name, b_entry in s_entry["elevation_bands"].items():
                b_tot = b_entry["total"]
                b_succ = b_entry["success"]
                b_entry["rate"] = (b_succ / b_tot) if b_tot > 0 else 0.0

        return stats

    def compute_sky_heatmap(self) -> dict:
        """
        全復調成功パスの軌道点群 (Az, El) を集計し、
        天球グリッドセル (Az: 10°ステップ, El: 5°ステップ) の通過カウントと
        方位角ごとの実効開口境界 (horizon_polygon) を算出。
        """
        if self._heatmap_cache is not None:
            return self._heatmap_cache

        if not self._passes and self.s3_client:
            try:
                self.get_all_passes()
            except Exception:
                pass

        grid_counts = {}
        az_elevations = {b: [] for b in range(36)}
        successful_passes_count = 0

        for pass_id, pdata in self._passes.items():
            summary = pdata.get("summary") or {}
            packets_count = summary.get("packets_count", 0)
            files = pdata.get("files") or {}
            has_images = any(f.lower().endswith(".png") and "spectrogram" not in f.lower() for f in files)
            if not (packets_count > 0 or has_images):
                continue

            pid = pdata.get("pass_id") or pass_id
            sat = pdata.get("satellite")
            parsed_sat, parsed_dt = parse_pass_id(pid)
            if not sat:
                sat = parsed_sat
            if not sat:
                continue

            start_time = parsed_dt or pdata.get("last_modified_dt")
            if not start_time:
                continue

            dur = int(float(summary.get("duration_seconds", 406.0)))
            tle = load_tle_for_satellite(sat)
            if not tle:
                continue

            try:
                calc = OrbitCalculator()
                pts = calc.calculate_trajectory(tle[0], tle[1], start_time, dur, step_seconds=5)
                valid_pts = [p for p in pts if p.el >= 10.0]
                if not valid_pts:
                    continue

                successful_passes_count += 1
                for p in valid_pts:
                    norm_az = p.az % 360.0
                    az_bin = int(norm_az // 10.0) % 36
                    el_bin = int(max(0.0, min(89.9, p.el)) // 5.0)

                    cell_key = (az_bin, el_bin)
                    grid_counts[cell_key] = grid_counts.get(cell_key, 0) + 1
                    az_elevations[az_bin].append(p.el)
            except Exception:
                pass

        cells = []
        for (az_b, el_b), cnt in grid_counts.items():
            cells.append({
                "az": az_b * 10,
                "el": el_b * 5,
                "az_center": az_b * 10 + 5.0,
                "el_center": el_b * 5 + 2.5,
                "count": cnt,
            })

        horizon_polygon = []
        empirical_mask = {}
        for b in range(36):
            az_deg = b * 10
            els = az_elevations[b]
            if els:
                el_min = round(min(els), 1)
                el_max = round(max(els), 1)
                horizon_polygon.append({
                    "az": az_deg,
                    "el_min": el_min,
                    "el_max": el_max,
                })
                empirical_mask[b] = (el_min, el_max)

        result = {
            "total_successful_passes": successful_passes_count,
            "cells": cells,
            "horizon_polygon": horizon_polygon,
            "empirical_mask": empirical_mask,
        }
        if self._passes:
            self._heatmap_cache = result
        return result


pass_cache = PassDataCache(s3_client, S3_BUCKET)


class DashboardHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            local_file = os.path.join(os.path.dirname(__file__), "static", rel_path)
            if os.path.isfile(local_file):
                ctype = "application/javascript" if local_file.endswith(".js") else "application/octet-stream"
                with open(local_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_error(404, "Static file not found")
            return

        if path == "/api/passes":
            try:
                page = max(1, int(query.get("page", ["1"])[0]))
            except ValueError:
                page = 1
            try:
                limit = max(1, min(100, int(query.get("limit", ["12"])[0])))
            except ValueError:
                limit = 12
            category = query.get("category", ["all"])[0]
            has_data_param = query.get("has_data", ["false"])[0].lower() in ["true", "1", "yes"]

            all_passes = pass_cache.get_all_passes()

            counts = {"all": 0, "spacestation": 0, "weathersatellite": 0, "cubesat": 0}
            for p in all_passes:
                st = get_satellite_type(p).lower()
                counts["all"] += 1
                if st in counts:
                    counts[st] += 1

            matched_passes = []
            for p in all_passes:
                st = get_satellite_type(p)
                if category != "all" and st.lower() != category.lower():
                    continue
                if has_data_param:
                    summary = p.get("summary") or {}
                    packets_count = summary.get("packets_count", 0)
                    if packets_count == 0 and st not in ["WeatherSatellite", "CubeSat"]:
                        continue
                matched_passes.append(p)

            total_matched = len(matched_passes)
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            page_items = matched_passes[start_idx:end_idx]
            has_more = end_idx < total_matched

            cards_html = "".join(render_card_html(p) for p in page_items)

            body = json.dumps({
                "cards_html": cards_html,
                "has_more": has_more,
                "total_matched": total_matched,
                "counts": counts
            }).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/pending-tasks":
            pending_count = 0
            try:
                resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="raw/")
                for item in resp.get("Contents", []):
                    if any(item["Key"].lower().endswith(ext) for ext in (".wav", ".u8", ".raw", ".cadu")):
                        pending_count += 1
            except Exception as e:
                print(f"Error checking pending tasks: {e}", flush=True)

            body = json.dumps({"pending_count": pending_count}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/api/passes/") and path.endswith("/trajectory"):
            pass_id = urllib.parse.unquote(path[len("/api/passes/"):-len("/trajectory")])
            data = generate_trajectory_data(pass_id, pass_cache)
            if not data:
                self.send_error(404, f"Trajectory data or TLE not found for pass {pass_id}")
                return
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=300")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/future-passes":
            data = generate_future_passes_data(cache=pass_cache)
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=60")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/sky-mask/heatmap":
            data = pass_cache.compute_sky_heatmap()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=60")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/backup/stats":
            stats = get_s3_backup_stats(s3_client, S3_BUCKET)
            body = json.dumps(stats).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/backup/download":
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"radio_astronomy_backup_{ts_str}.tar"
            self.send_response(200)
            self.send_header("Content-Type", "application/x-tar")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            try:
                for chunk in generate_s3_backup_stream(s3_client, S3_BUCKET):
                    self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                print(f"Error streaming backup: {e}", flush=True)
            return

        if path == "/metrics":
            pending_count = 0
            try:
                resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="raw/")
                for item in resp.get("Contents", []):
                    if any(item["Key"].lower().endswith(ext) for ext in (".wav", ".u8", ".raw", ".cadu")):
                        pending_count += 1
            except Exception:
                pass

            metrics_text = (
                f"# HELP satellite_pending_analysis_tasks Number of raw satellite recordings waiting for analysis\n"
                f"# TYPE satellite_pending_analysis_tasks gauge\n"
                f"satellite_pending_analysis_tasks {pending_count}\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(metrics_text)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(metrics_text)
            return

        if path == "/view":
            key = query.get("key", [None])[0]
            if not key:
                self.send_error(400, "Missing key parameter")
                return
            try:
                obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                content_type = obj.get("ContentType", "application/octet-stream")
                if key.endswith(".png"):
                    content_type = "image/png"
                elif key.endswith(".json"):
                    content_type = "application/json"

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(obj["ContentLength"]))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                
                body = obj["Body"]
                while chunk := body.read(65536):
                    self.wfile.write(chunk)
            except Exception as e:
                self.send_error(404, f"Object not found: {e}")
            return

        if path == "/content":
            key = query.get("key", [None])[0]
            if not key:
                self.send_error(400, "Missing key parameter")
                return
            try:
                obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                data = obj["Body"].read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(404, f"Object read error: {e}")
            return

        if path == "/download":
            key = query.get("key", [None])[0]
            if not key:
                self.send_error(400, "Missing key parameter")
                return
            try:
                obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                filename = os.path.basename(key)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(obj["ContentLength"]))
                self.end_headers()
                
                body = obj["Body"]
                while chunk := body.read(65536):
                    self.wfile.write(chunk)
            except Exception as e:
                self.send_error(404, f"Object not found: {e}")
            return

        if path == "/" or path == "/index.html":
            self.render_dashboard()
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/backup/restore":
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self.send_error(400, "Empty restore payload")
                return

            restored_count = 0
            restored_bytes = 0
            try:
                class LimitedReader:
                    def __init__(self, rfile, limit):
                        self.rfile = rfile
                        self.limit = limit
                        self.read_so_far = 0

                    def read(self, size=-1):
                        if self.read_so_far >= self.limit:
                            return b""
                        if size < 0:
                            to_read = self.limit - self.read_so_far
                        else:
                            to_read = min(size, self.limit - self.read_so_far)
                        chunk = self.rfile.read(to_read)
                        self.read_so_far += len(chunk)
                        return chunk

                limited_stream = LimitedReader(self.rfile, content_length)
                with tarfile.open(fileobj=limited_stream, mode="r|*") as tf:
                    for member in tf:
                        if not member.isfile():
                            continue
                        name = member.name.lstrip("/")
                        if ".." in name or not any(name.startswith(p) for p in ("results/", "archive/", "golden/", "raw/")):
                            continue
                        f = tf.extractfile(member)
                        if f:
                            s3_client.upload_fileobj(f, Bucket=S3_BUCKET, Key=name)
                            restored_count += 1
                            restored_bytes += member.size

                pass_cache.last_sync_time = 0.0

                body = json.dumps({
                    "status": "ok",
                    "restored_count": restored_count,
                    "restored_bytes": restored_bytes
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                print(f"Error during restore: {e}", flush=True)
                err_body = json.dumps({"status": "error", "error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
            return

        self.send_error(404, "Not Found")

    def render_dashboard(self):
        try:
            body = HTML_TEMPLATE.replace("__BUCKET__", S3_BUCKET)
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as e:
            self.send_error(500, f"Dashboard render error: {e}")

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


async def run_dispatcher():
    print(f"📡 [Dispatcher] Background dispatcher started (Temporal: {TEMPORAL_HOST})", flush=True)
    temporal_client = None

    while True:
        try:
            if temporal_client is None:
                try:
                    temporal_client = await Client.connect(TEMPORAL_HOST)
                    print(f"✅ [Dispatcher] Connected to Temporal Server at {TEMPORAL_HOST}", flush=True)
                except Exception as e:
                    await asyncio.sleep(5)
                    continue

            # S3 の raw/ プレフィックスをスキャン
            resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="raw/")
            for item in resp.get("Contents", []):
                key = item["Key"]
                if not any(key.lower().endswith(ext) for ext in (".wav", ".u8", ".raw", ".cadu")):
                    continue
                parts = key.split("/")
                if len(parts) >= 3:
                    satellite = parts[1]
                    filename = parts[2]
                    pass_id = os.path.splitext(filename)[0]

                    if pass_id in triggered_passes:
                        continue

                    # 既に results/<satellite>/<pass_id>/summary.json が存在するか確認
                    summary_key = f"results/{satellite}/{pass_id}/summary.json"
                    try:
                        s3_client.head_object(Bucket=S3_BUCKET, Key=summary_key)
                        triggered_passes.add(pass_id)
                        continue
                    except Exception:
                        pass  # 未解析の生録音！

                    print(f"🚀 [Dispatcher] Found new raw recording: {key} ({satellite} / {pass_id})", flush=True)
                    print(f"⏳ [Dispatcher] Starting Temporal workflow for {pass_id}...", flush=True)

                    try:
                        import sys
                        if "/app/src" not in sys.path:
                            sys.path.insert(0, "/app/src")
                        from workflows import AnalyzeSatellitePassWorkflow, PassAnalysisParams
                        await temporal_client.start_workflow(
                            AnalyzeSatellitePassWorkflow.run,
                            PassAnalysisParams(
                                s3_key=key,
                                satellite=satellite,
                                pass_id=pass_id,
                                bucket_name=S3_BUCKET,
                                keep_raw=False
                            ),
                            id=f"analyze-{pass_id}",
                            task_queue=TASK_QUEUE
                        )
                        triggered_passes.add(pass_id)
                        print(f"🎉 [Dispatcher] Workflow started successfully for {pass_id}!", flush=True)
                    except Exception as wf_err:
                        if "already running" in str(wf_err).lower() or "already started" in str(wf_err).lower():
                            triggered_passes.add(pass_id)
                        print(f"⚠️ [Dispatcher] Failed to start workflow: {wf_err}", flush=True)

        except Exception as e:
            print(f"⚠️ [Dispatcher Error]: {e}", flush=True)

        await asyncio.sleep(8)


def start_background_dispatcher():
    if not ENABLE_AUTO_DISPATCH:
        print("ℹ️ [Dispatcher] Auto-dispatch is disabled by config.", flush=True)
        return

    def loop_runner():
        while True:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(run_dispatcher())
            except Exception as e:
                print(f"❌ [Dispatcher Thread Crashed]: {e}", flush=True)
                import traceback
                traceback.print_exc()
                import time
                time.sleep(5)

    t = threading.Thread(target=loop_runner, daemon=True)
    t.start()


if __name__ == "__main__":
    start_background_dispatcher()
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, DashboardHandler)
    print(f"🚀 Satellite Viewer listening on http://0.0.0.0:{PORT} (Bucket: {S3_BUCKET})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()
