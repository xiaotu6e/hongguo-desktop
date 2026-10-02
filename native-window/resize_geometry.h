#pragma once
#include <algorithm>
#include <cmath>

namespace hongguo {
struct Rect { int left, top, right, bottom; };
enum Edge { None = 0, Left = 1, Right = 2, Top = 4, Bottom = 8 };

inline int ResizeEdge(Rect r, int x, int y, int border, int corner) {
    if (x < r.left || x >= r.right || y < r.top || y >= r.bottom) return None;
    const bool left = x-r.left < corner, right = r.right-x <= corner;
    const bool top = y-r.top < corner, bottom = r.bottom-y <= corner;
    if ((left || right) && (top || bottom))
        return (left ? Left : Right) | (top ? Top : Bottom);
    if (x-r.left < border) return Left;
    if (r.right-x <= border) return Right;
    if (y-r.top < border) return Top;
    if (r.bottom-y <= border) return Bottom;
    return None;
}

// Opposite sides/corners stay anchored. Diagonal motion projects onto the
// aspect-ratio line, so horizontal and vertical pointer movement both count.
inline Rect ResizeRect(Rect start, Rect work, int edge, int dx, int dy, int minimumShortSide) {
    if (!edge || start.right <= start.left || start.bottom <= start.top) return start;
    const double ratio = start.right-start.left > start.bottom-start.top ? 16.0/9 : 9.0/16;
    const int sx = edge & Left ? -1 : edge & Right ? 1 : 0;
    const int sy = edge & Top ? -1 : edge & Bottom ? 1 : 0;
    const double cx = (start.left+start.right)/2.0, cy = (start.top+start.bottom)/2.0;
    const double anchorX = sx < 0 ? start.right : sx > 0 ? start.left : cx;
    const double anchorY = sy < 0 ? start.bottom : sy > 0 ? start.top : cy;
    double height = start.bottom-start.top;
    if (sx && sy) height += (sx*dx*ratio + sy*dy)/(ratio*ratio+1);
    else if (sx) height = (start.right-start.left+sx*dx)/ratio;
    else height += sy*dy;
    const double availableWidth = sx < 0 ? anchorX-work.left : sx > 0 ? work.right-anchorX :
        2*std::min(cx-work.left, work.right-cx);
    const double availableHeight = sy < 0 ? anchorY-work.top : sy > 0 ? work.bottom-anchorY :
        2*std::min(cy-work.top, work.bottom-cy);
    const double maximum = std::max(1.0, std::min(availableHeight, availableWidth/ratio));
    const double minimum = std::min(maximum, minimumShortSide/std::min(1.0, ratio));
    height = std::clamp(height, minimum, maximum);
    const int h = std::max(1, static_cast<int>(std::floor(height)));
    const int w = std::max(1, static_cast<int>(std::round(h*ratio)));
    const int left = static_cast<int>(std::round(sx < 0 ? anchorX-w : sx > 0 ? anchorX : cx-w/2.0));
    const int top = static_cast<int>(std::round(sy < 0 ? anchorY-h : sy > 0 ? anchorY : cy-h/2.0));
    return {left, top, left+w, top+h};
}
}
