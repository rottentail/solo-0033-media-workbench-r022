"""JSON HTTP interface for a trusted operator media processing service."""
from contextlib import closing
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from . import assets, db, timeline, renders, worker, jobs, collab_store
from . import audio_layers, mix_plan, mix_render
from . import media_sources, proxies, relinking, proxy_store
from . import transition_plan, transition_render, retime_plan, retime_render
from . import multicam_plan, multicam_render
from . import spatial_plan, spatial_render
from . import bundle_export
from . import caption_words, caption_plan
from . import denoise_plan, denoise_render
from . import room_plan, room_render
from . import music_plan, music_render
from . import master_plan, master_render
from . import color_plan, color_render
from . import stretch_plan, stretch_render
from . import resample_plan, resample_render
from . import repair_plan, repair_render
from . import echo_plan, echo_render
from . import stitch_plan, stitch_render
from . import sphere_plan, sphere_render
from . import rolling_plan, rolling_render
from . import lens_plan, lens_render


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            path = urlparse(self.path).path.strip('/').split('/')
            with closing(db.connect(self.server.database)) as conn:
                if len(path) == 2 and path[0] == 'projects':
                    result = timeline.snapshot(conn, int(path[1]))
                elif len(path) == 2 and path[0] in ('renders', 'render-jobs'):
                    result = renders.get(conn, int(path[1]))
                elif len(path) == 3 and path[0] == 'projects' and path[2] == 'edits':
                    result = collab_store.view(conn, int(path[1]))
                elif len(path) == 3 and path[0] == 'projects' and path[2] == 'audio':
                    result = audio_layers.snapshot(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'mix-plans':
                    result = mix_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'assets':
                    result = media_sources.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'retime-plans':
                    result = retime_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'transition-plans':
                    result = transition_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'multicam-plans':
                    result = multicam_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'spatial-plans':
                    result = spatial_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'caption-plans':
                    result = caption_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'lens-plans':
                    result = lens_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'lens-profiles':
                    result = stitch_plan.load(conn, 'lens_profiles', int(path[1]))
                elif len(path) == 2 and path[0] == 'rolling-plans':
                    result = rolling_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'sphere-plans':
                    result = sphere_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'stitch-plans':
                    result = stitch_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'stitch-profiles':
                    result = stitch_plan.load(conn, 'stitch_profiles', int(path[1]))
                elif len(path) == 2 and path[0] == 'echo-plans':
                    result = echo_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'echo-profiles':
                    result = echo_plan.load(conn, 'echo_profiles', int(path[1]))
                elif len(path) == 2 and path[0] == 'repair-plans':
                    result = repair_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'resample-plans':
                    result = resample_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'stretch-plans':
                    result = stretch_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'color-plans':
                    result = color_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'master-plans':
                    result = master_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'music-plans':
                    result = music_plan.get(conn,int(path[1]))
                elif len(path) == 2 and path[0] == 'room-plans':
                    result = room_plan.get(conn, int(path[1]))
                elif len(path) == 2 and path[0] == 'denoise-plans':
                    result = denoise_plan.get(conn, int(path[1]))
                elif len(path) == 3 and path[0] == 'assets' and path[2] == 'transcript':
                    result = caption_words.get(conn, int(path[1]))
                else:
                    return self.respond(404, {'error': 'not found'})
            self.respond(200, result)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.respond(400, {'error': str(exc)})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('invalid request size')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('JSON object required')
            path = urlparse(self.path).path.strip('/').split('/')
            with closing(db.connect(self.server.database)) as conn:
                if path == ['assets']:
                    result = assets.register(conn, data['path'])
                elif len(path)==3 and path[0]=='projects' and path[2]=='music':
                    result = music_plan.catalogue(conn,int(path[1]),data['sections'],data['joins'],data['expected_revision'])
                elif path == ['lens-profiles']:
                    result = lens_plan.profile(conn, data['asset_id'], data['focal_px'], data['centre'],
                        data['matches'], data.get('threshold_px', .25))
                elif path == ['lens-plans']:
                    result = lens_plan.create(conn, data['profile_id'], data['start_ms'], data['duration_ms'], data['zoom'])
                elif len(path)==3 and path[0]=='lens-plans' and path[2] in ('render','preview'):
                    result = lens_render.render(conn, int(path[1]), data['destination'], data.get('at_frame',0), data.get('frame_count'))
                elif path == ['rolling-plans']:
                    result = rolling_plan.create(conn, data['asset_id'], data['start_ms'], data['duration_ms'],
                        data['horizontal_fov'], data['zoom'], data['readout_ms'], data['direction'], data['gyro'])
                elif len(path)==3 and path[0]=='rolling-plans' and path[2] in ('render','preview'):
                    result = rolling_render.render(conn, int(path[1]), data['destination'], data.get('at_frame',0), data.get('frame_count'))
                elif path == ['sphere-plans']:
                    result = sphere_plan.create(conn, data['asset_id'], data['start_ms'], data['duration_ms'],
                        data['width'], data['height'], data['horizontal_fov'], data['keys'])
                elif len(path)==3 and path[0]=='sphere-plans' and path[2] in ('render','preview'):
                    result = sphere_render.render(conn, int(path[1]), data['destination'], data.get('at_frame',0), data.get('frame_count'))
                elif path == ['stitch-profiles']:
                    result = stitch_plan.profile(conn,data['left_asset_id'],data['right_asset_id'],data['matches'],data.get('threshold_px',1.0))
                elif path == ['stitch-plans']:
                    result = stitch_plan.create(conn,data['profile_id'],data['left_start_ms'],data['right_start_ms'],data['duration_ms'],data['canvas'])
                elif len(path)==3 and path[0]=='stitch-plans' and path[2] in ('render','preview'):
                    result = stitch_render.render(conn,int(path[1]),data['destination'],data.get('at_frame',0),data.get('frame_count'))
                elif path == ['echo-profiles']:
                    result = echo_plan.profile(conn,data['microphone_id'],data['reference_id'],data['start_ms'],data['end_ms'],data.get('offset_samples',0))
                elif path == ['echo-plans']:
                    result = echo_plan.create(conn,data['project_id'],data['profile_ids'])
                elif len(path)==3 and path[0]=='echo-plans' and path[2] in ('render','audition'):
                    result = echo_render.render(conn,int(path[1]),data['destination'],data.get('at_sample',0),data.get('samples'),data.get('block_frames',4096))
                elif path == ['repair-marks']:
                    result = repair_plan.marks(conn,data['asset_id'],data['intervals'],data['expected_revision'])
                elif path == ['repair-plans']:
                    result = repair_plan.create(conn,data['project_id'])
                elif len(path)==3 and path[0]=='repair-plans' and path[2] in ('render','audition'):
                    result = repair_render.render(conn,int(path[1]),data['destination'],data.get('at_sample',0),data.get('samples'),data.get('block_frames',4096))
                elif path == ['resample-plans']:
                    result = resample_plan.create(conn, data['project_id'], data.get('rate',44100))
                elif len(path)==3 and path[0]=='resample-plans' and path[2] in ('render','audition'):
                    result = resample_render.render(conn,int(path[1]),data['destination'],data.get('at_sample',0),data.get('samples'),data.get('block_frames',4096))
                elif path == ['stretch-plans']:
                    result = stretch_plan.create(conn, data['project_id'], data['maps'])
                elif len(path)==3 and path[0]=='stretch-plans' and path[2] in ('render','audition'):
                    result = stretch_render.render(conn,int(path[1]),data['destination'],data.get('at_sample',0),data.get('samples'),data.get('block_frames',4096))
                elif path == ['color-plans']:
                    result = color_plan.create(conn,data['project_id'],data['encodings'],data.get('white_nits',100))
                elif len(path)==3 and path[0]=='color-plans' and path[2] in ('render','preview'):
                    result = color_render.render(conn,int(path[1]),data['destination'],data.get('at_frame',0),data.get('frame_count'))
                elif path == ['master-plans']:
                    result = master_plan.create(conn, data['mix_id'], data.get('target_lufs',-23), data.get('ceiling_db',-1))
                elif len(path)==3 and path[0]=='master-plans' and path[2] in ('render','audition'):
                    result = master_render.render(conn,int(path[1]),data['destination'],data.get('at_sample',0),data.get('samples'),data.get('block_frames',4096))
                elif path == ['music-plans']:
                    result = music_plan.create(conn,data['project_id'],data['target_ms'],data.get('overlap_ms',0),data['starts'],data['ends'])
                elif len(path)==3 and path[0]=='music-plans' and path[2] in ('render','audition'):
                    result = music_render.render(conn,int(path[1]),data['destination'],data.get('at_sample',0),data.get('samples'),data.get('block_frames',4096))
                elif path == ['room-plans']:
                    result = room_plan.create(conn,data['project_id'],data['stages'],data.get('dry',0),data.get('wet',1))
                elif len(path)==3 and path[0]=='room-plans' and path[2] in ('render','audition'):
                    result = room_render.render(conn,int(path[1]),data['destination'],
                        data.get('at_sample',0),data.get('samples'),data.get('block_frames',4096))
                elif path == ['noise-profiles']:
                    result = denoise_plan.profile(conn, data['asset_id'], data['start_ms'], data['end_ms'])
                elif path == ['denoise-plans']:
                    result = denoise_plan.create(conn, data['project_id'], data['profile_id'],
                        data.get('strength',1.0), data.get('floor_db',-24), data.get('release_ms',80))
                elif len(path) == 3 and path[0] == 'denoise-plans' and path[2] in ('render','audition'):
                    result = denoise_render.render(conn, int(path[1]), data['destination'],
                        data.get('at_ms',0), data.get('duration_ms'), data.get('block_frames',4096))
                elif len(path) == 3 and path[0] == 'assets' and path[2] == 'transcript':
                    result = caption_words.replace(conn, int(path[1]), data['words'], data['expected_revision'])
                elif path == ['caption-plans']:
                    result = caption_plan.create(conn, data['project_id'], data.get('width',32),
                        data.get('max_cues',32), data.get('minimum_ms',400),
                        data.get('maximum_ms',5000), data.get('hold_ms',500))
                elif len(path) == 3 and path[0] == 'caption-plans' and path[2] == 'preview':
                    result = caption_plan.preview(conn, int(path[1]), data['at_ms'])
                elif len(path) == 3 and path[0] == 'caption-plans' and path[2] == 'export':
                    result = caption_plan.export(conn, int(path[1]), data['destination'])
                elif path == ['offline-bundles']:
                    result = bundle_export.create(conn, data['projects'], data['max_media_bytes'], data['destination'])
                elif path == ['offline-bundle-sets']:
                    result = bundle_export.create_set(conn, data['projects'], data['volumes'])
                elif path == ['proxy-store', 'collect']:
                    result = proxy_store.collect(conn, data['directory'], data['max_bytes'])
                elif path == ['asset-relinks']:
                    result = relinking.replace(conn, data['locations'])
                elif len(path) == 3 and path[0] == 'assets' and path[2] == 'proxy':
                    result = proxies.create(conn, int(path[1]), data['directory'])
                elif len(path) == 3 and path[0] == 'projects' and path[2] == 'preview':
                    result = proxies.preview(conn, int(path[1]), data['destination'])
                elif path == ['projects']:
                    result = timeline.project(conn, data['name'])
                elif path == ['clips']:
                    result = timeline.add_clip(conn, data['project_id'], data['asset_id'],
                        data['start_ms'], data['end_ms'], data.get('gain', 1))
                elif len(path) == 3 and path[0] == 'projects' and path[2] == 'timeline':
                    result = timeline.replace(conn, int(path[1]), data['clips'], data['expected_revision'])
                elif len(path) == 3 and path[0] == 'projects' and path[2] == 'edits':
                    result = collab_store.commit(conn, int(path[1]), data['actor'], data['request_id'],
                        data.get('base_revision'), data.get('actions'), data.get('undo_request'))
                elif len(path) == 3 and path[0] == 'projects' and path[2] == 'voices':
                    result = audio_layers.replace(conn, int(path[1]), data['voices'], data['expected_revision'])
                elif path == ['mix-plans']:
                    result = mix_plan.create(conn, data['project_id'], data.get('threshold_db', -38),
                                            data.get('attack_ms', 40), data.get('release_ms', 160))
                elif path == ['retime-plans']:
                    result = retime_plan.create(conn, data['project_id'], data['curves'])
                elif path == ['multicam-plans']:
                    result = multicam_plan.create(conn, data['project_id'], data['cameras'],
                        data['master_asset_id'], data['duration_ms'], data.get('minimum_shot_ms',400),
                        data.get('max_cuts',8), data.get('cut_cost',0))
                elif path == ['spatial-plans']:
                    result = spatial_plan.create(conn, data['project_id'], data['asset_id'],
                        data['start_ms'], data['end_ms'], data['convention'], data['keyframes'],
                        data.get('max_poses',64))
                elif len(path) == 3 and path[0] == 'spatial-plans' and path[2] == 'render':
                    result = spatial_render.render(conn, int(path[1]), data['destination'])
                elif len(path) == 3 and path[0] == 'spatial-plans' and path[2] == 'audition':
                    result = spatial_render.render(conn, int(path[1]), data['destination'],
                        data['at_ms'], data['duration_ms'])
                elif len(path) == 3 and path[0] == 'multicam-plans' and path[2] == 'render':
                    result = multicam_render.render(conn, int(path[1]), data['destination'])
                elif len(path) == 3 and path[0] == 'retime-plans' and path[2] == 'render':
                    result = retime_render.render(conn, int(path[1]), data['destination'])
                elif path == ['transition-plans']:
                    result = transition_plan.create(conn, data['project_id'], data['overlaps_ms'])
                elif len(path) == 3 and path[0] == 'transition-plans' and path[2] == 'render':
                    result = transition_render.render(conn, int(path[1]), data['destination'])
                elif len(path) == 3 and path[0] == 'mix-plans' and path[2] == 'render':
                    result = mix_render.render(conn, int(path[1]), data['destination'])
                elif path == ['renders']:
                    result = renders.create(conn, data['project_id'], data['destination'])
                elif path == ['render-jobs']:
                    result = renders.queue(conn, data['project_id'], data['destination'])
                elif len(path) == 3 and path[0] == 'render-jobs':
                    render_id = int(path[1])
                    if path[2] == 'run':
                        result = worker.run(conn, render_id)
                    elif path[2] == 'cancel':
                        result = renders.cancel(conn, render_id)
                    elif path[2] == 'retry':
                        result = jobs.retry(conn, render_id)
                    else:
                        return self.respond(404, {'error': 'not found'})
                else:
                    return self.respond(404, {'error': 'not found'})
            self.respond(201, result)
        except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
            self.respond(400, {'error': str(exc)})


def serve():
    server = ThreadingHTTPServer(('127.0.0.1', int(os.getenv('MEDIA_PORT', '8765'))), Handler)
    server.database = os.getenv('MEDIA_DATABASE', 'media-workbench.sqlite3')
    print(f'Listening on http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    serve()
