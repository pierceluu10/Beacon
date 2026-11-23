// Run from the repo root: swift design/drawlogo.swift .
// Draws Beacon's mark: a notched pointer cut out of an amber light. Writes logo.png (app tile) and mark.png (menu bar).
import AppKit

let out = CommandLine.arguments[1]
let tileColor = NSColor(srgbRed: 0.067, green: 0.071, blue: 0.078, alpha: 1)   // #111214
let lightColor = NSColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)       // #FFB020
let cursorColor = NSColor(srgbRed: 0.969, green: 0.969, blue: 0.961, alpha: 1) // #F7F7F5

// Glyph units: cursor tip at the origin, y down.
let gap: CGFloat = 20
let lightCenter = CGPoint(x: -19, y: -19), lightRadius: CGFloat = 94
let tileGlyphSize: CGFloat = 270
let opticalShift = CGPoint(x: 0, y: -2)
let markHeight = 36  // 18 pt at 2x

/// Polygon with a per-corner radius: (x, y, radius).
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

// A symmetric arrowhead with a notch where the tail would be.
let cursor: CGPath = roundedPolygon([(0, 0, 10), (0, 190, 10), (48, 112, 4), (137, 129, 10)])
let lightBounds = CGRect(x: lightCenter.x - lightRadius, y: lightCenter.y - lightRadius, width: lightRadius * 2, height: lightRadius * 2)
let bounds = lightBounds.union(cursor.boundingBoxOfPath)

/// The light, then the cursor knocked out of it by a gap so the two shapes read separately.
/// A nil gap colour clears the gap instead, which a template image needs.
func glyph(_ context: CGContext, light: NSColor, cursor cursorFill: NSColor, gapColor: NSColor?) {
    context.setFillColor(light.cgColor)
    context.fillEllipse(in: lightBounds)
    context.setLineJoin(.round)
    context.addPath(cursor); context.setLineWidth(gap * 2)
    if let gapColor { context.setStrokeColor(gapColor.cgColor) } else { context.setBlendMode(.clear) }
    context.strokePath()
    context.setBlendMode(.normal)
    context.addPath(cursor); context.setFillColor(cursorFill.cgColor); context.fillPath()
}

func render(width: Int, height: Int, _ draw: (CGContext) -> Void) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    context.translateBy(x: 0, y: CGFloat(height)); context.scaleBy(x: 1, y: -1)  // y down, like the glyph's coordinates
    draw(context)
    return rep
}

func save(_ rep: NSBitmapImageRep, _ name: String) {
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out).appendingPathComponent(name))
}

let tile = render(width: 512, height: 512) { context in
    let square = CGPath(roundedRect: CGRect(x: 50, y: 50, width: 412, height: 412), cornerWidth: 92, cornerHeight: 92, transform: nil)
    context.addPath(square); context.setFillColor(tileColor.cgColor); context.fillPath()
    let scale = tileGlyphSize / max(bounds.width, bounds.height)
    context.translateBy(x: 256 + opticalShift.x - bounds.midX * scale, y: 256 + opticalShift.y - bounds.midY * scale)
    context.scaleBy(x: scale, y: scale)
    glyph(context, light: lightColor, cursor: cursorColor, gapColor: tileColor)
}
save(tile, "logo.png")

let markScale = CGFloat(markHeight) / bounds.height
let mark = render(width: Int((bounds.width * markScale).rounded(.up)), height: markHeight) { context in
    context.scaleBy(x: markScale, y: markScale); context.translateBy(x: -bounds.minX, y: -bounds.minY)
    glyph(context, light: .black, cursor: .black, gapColor: nil)
}
save(mark, "mark.png")
