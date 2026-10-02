#include "../native-window/resize_geometry.h"
#include <cstdlib>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    if (argc < 2) return 2;
    auto number = [&](int i) { return std::atoi(argv[i]); };
    hongguo::Rect r{number(2), number(3), number(4), number(5)};
    if (std::string(argv[1]) == "hit" && argc == 10) {
        std::cout << hongguo::ResizeEdge(r, number(6), number(7), number(8), number(9));
    } else if (std::string(argv[1]) == "resize" && argc == 14) {
        auto result = hongguo::ResizeRect(r, {number(6),number(7),number(8),number(9)},
                                        number(10),number(11),number(12),number(13));
        std::cout << result.left << ' ' << result.top << ' ' << result.right << ' ' << result.bottom;
    } else return 2;
    return 0;
}
