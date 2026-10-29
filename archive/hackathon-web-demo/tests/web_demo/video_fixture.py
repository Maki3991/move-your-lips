from fractions import Fraction
import av


def video_bytes(path):
    with av.open(str(path), "w", format="webm") as container:
        stream = container.add_stream("libvpx", rate=25)
        stream.width, stream.height, stream.pix_fmt = 160, 120, "yuv420p"
        for index in range(25):
            frame = av.VideoFrame(160, 120, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes([96]) * plane.buffer_size)
            frame.pts, frame.time_base = index, Fraction(1, 25)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path.read_bytes()
