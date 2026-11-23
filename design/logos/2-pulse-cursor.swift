// Concept 3: cursor pinging a beacon ring: its tip marks the center of the ring.
import AppKit

let out = CommandLine.arguments[1]
let name = "concept-3"
let white = CGColor(srgbRed: 0.96, green: 0.96, blue: 0.97, alpha: 1)
let amber = CGColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)
let ink = CGColor(srgbRed: 0.075, green: 0.078, blue: 0.094, alpha: 1)
// Design space is the 1024 macOS icon grid: 824 tile, glyph fits inside `glyphBox`.
let grid: CGFloat = 1024, tileSize: CGFloat = 824, tileRadius: CGFloat = 185, glyphBox: CGFloat = 520
let weight: CGFloat = 56  // shared stroke weight for arcs; pointer corners are rounded by the same radius family
let opticalShift = CGPoint(x: 0, y: 0)

func polygon(_ points: [CGPoint], corner: CGFloat) -> CGPath {
    let path = CGMutablePath(); path.addLines(between: points); path.closeSubpath()
    return path.union(path.copy(strokingWithWidth: corner, lineCap: .round, lineJoin: .round, miterLimit: 1))
}

// y points down. The cursor's tip sits at the center of a beacon ring, with click ripples spreading from it.
let tip = CGPoint.zero
let kite = [tip, CGPoint(x: 0, y: 400), CGPoint(x: 100, y: 248), CGPoint(x: 285, y: 285)]
let ringRadii: [CGFloat] = [130, 250]
let moat: CGFloat = 40  // clear space between the cursor's edge and a ring's cap
let cursorSpan: (CGFloat, CGFloat) = (45, 90)  // y-down angles of the cursor's two edges leaving the tip

// A ripple centered on the tip, broken where the cursor body passes, with round caps at the break.
func ripple(_ radius: CGFloat) -> CGPath {
    let clearance = asin(min(1, (moat + weight / 2 + 14) / radius)) * 180 / .pi
    let path = CGMutablePath()
    path.addArc(center: tip, radius: radius, startAngle: (cursorSpan.1 + clearance) * .pi / 180,
                endAngle: (cursorSpan.0 - clearance + 360) * .pi / 180, clockwise: false)
    return path.copy(strokingWithWidth: weight, lineCap: .round, lineJoin: .round, miterLimit: 1)
}

let shapes: [(CGPath, CGColor)] = ringRadii.map { (ripple($0), amber) } + [(polygon(kite, corner: 28), white)]

func render(_ size: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    context.translateBy(x: 0, y: CGFloat(size)); context.scaleBy(x: CGFloat(size) / grid, y: -CGFloat(size) / grid)
    let inset = (grid - tileSize) / 2
    context.addPath(CGPath(roundedRect: CGRect(x: inset, y: inset, width: tileSize, height: tileSize),
                           cornerWidth: tileRadius, cornerHeight: tileRadius, transform: nil))
    context.setFillColor(ink); context.fillPath()
    let bounds = shapes.map { $0.0.boundingBoxOfPath }.reduce(CGRect.null) { $0.union($1) }
    let scale = glyphBox / max(bounds.width, bounds.height)
    context.translateBy(x: grid / 2 + opticalShift.x, y: grid / 2 + opticalShift.y); context.scaleBy(x: scale, y: scale)
    context.translateBy(x: -bounds.midX, y: -bounds.midY)
    for (path, color) in shapes { context.addPath(path); context.setFillColor(color); context.fillPath() }
    return rep
}

for (size, suffix) in [(512, ""), (64, "-64")] {
    try! render(size).representation(using: .png, properties: [:])!
        .write(to: URL(fileURLWithPath: out).appendingPathComponent("\(name)\(suffix).png"))
}
