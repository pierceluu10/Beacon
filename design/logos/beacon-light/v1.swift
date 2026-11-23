import AppKit

let out = CommandLine.arguments[1]
let name = URL(fileURLWithPath: CommandLine.arguments[0]).deletingPathExtension().lastPathComponent
let tileColor = NSColor(srgbRed: 0.067, green: 0.071, blue: 0.078, alpha: 1)   // #111214
let lightColor = NSColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)       // #FFB020
let cursorColor = NSColor(srgbRed: 0.969, green: 0.969, blue: 0.961, alpha: 1) // #F7F7F5

let gap: CGFloat = 20
let lightCenter = CGPoint(x: -19, y: -19), lightRadius: CGFloat = 94
let glyphSize: CGFloat = 270

/// Polygon with a per-corner radius: (x, y, radius). Local units, cursor tip at the origin, y down.
func roundedPolygon(_ corners: [(CGFloat, CGFloat, CGFloat)]) -> CGMutablePath {
    let points = corners.map { CGPoint(x: $0.0, y: $0.1) }
    let path = CGMutablePath()
    path.move(to: CGPoint(x: (points.last!.x + points[0].x) / 2, y: (points.last!.y + points[0].y) / 2))
    for index in points.indices {
        path.addArc(tangent1End: points[index], tangent2End: points[(index + 1) % points.count], radius: corners[index].2)
    }
    path.closeSubpath()
    return path
}
// v1 "Droplet": a pointer whose whole rear is a circle, a teardrop tipped at the light so the cursor echoes the disc.
let cursor: CGPath = {
    let axis = atan2(0.916, 0.40), distance: CGFloat = 116, radius: CGFloat = 50
    let center = CGPoint(x: cos(axis) * distance, y: sin(axis) * distance)
    let spread = asin(radius / distance), reach = (distance * distance - radius * radius).squareRoot()
    let tangent = { (angle: CGFloat) in (cos(angle) * reach, sin(angle) * reach, CGFloat(0)) }
    let left = tangent(axis + spread), right = tangent(axis - spread)
    return roundedPolygon([(0, 0, 10), left, right])
        .union(CGPath(ellipseIn: CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2), transform: nil))
}()
let opticalShift = CGPoint(x: 0, y: -2)
func detail(_ context: CGContext) {}

func glyph(_ context: CGContext) {
    context.setFillColor(lightColor.cgColor)
    context.fillEllipse(in: CGRect(x: lightCenter.x - lightRadius, y: lightCenter.y - lightRadius, width: lightRadius * 2, height: lightRadius * 2))
    // The tile-coloured outline knocks the cursor out of the light so the two shapes read separately.
    context.setLineJoin(.round)
    context.addPath(cursor); context.setStrokeColor(tileColor.cgColor); context.setLineWidth(gap * 2); context.strokePath()
    context.addPath(cursor); context.setFillColor(cursorColor.cgColor); context.fillPath()
    detail(context)
}

func render(size: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    context.translateBy(x: 0, y: CGFloat(size)); context.scaleBy(x: 1, y: -1)
    context.scaleBy(x: CGFloat(size) / 512, y: CGFloat(size) / 512)
    let tile = CGPath(roundedRect: CGRect(x: 50, y: 50, width: 412, height: 412), cornerWidth: 92, cornerHeight: 92, transform: nil)
    context.addPath(tile); context.setFillColor(tileColor.cgColor); context.fillPath()
    let lightBounds = CGRect(x: lightCenter.x - lightRadius, y: lightCenter.y - lightRadius, width: lightRadius * 2, height: lightRadius * 2)
    let bounds = lightBounds.union(cursor.boundingBoxOfPath)
    let scale = glyphSize / max(bounds.width, bounds.height)
    context.translateBy(x: 256 + opticalShift.x - bounds.midX * scale, y: 256 + opticalShift.y - bounds.midY * scale)
    context.scaleBy(x: scale, y: scale)
    glyph(context)
    return rep
}

for (size, suffix) in [(512, ""), (64, "-64"), (16, "-16")] {
    try! render(size: size).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out).appendingPathComponent("\(name)\(suffix).png"))
}
