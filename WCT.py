#! /usr/bin/env python3

import os
import torch
import argparse
import pprint
from PIL import Image
from torch.autograd import Variable
import torchvision.utils as vutils
from Loader import gray_loader, DatasetOne
from util import *
import scipy.misc
import time


def join_path(*dirs):
    if len(dirs) == 0:
        return ''
    path = dirs[0]
    for d in dirs[1:]:
        path = os.path.join(path, d)
    return path


def make_filepath(fpath, dir_name=None, ext_name=None, tag=None):
    if dir_name is None:
        dir_name = os.path.dirname(fpath)
        if dir_name == '':
            dir_name = '.'
    fname = os.path.basename(fpath)
    base, ext = os.path.splitext(fname)
    if ext_name is None:
        ext_name = ext
    elif ext_name != '' and ext_name[0] != '.':
        ext_name = '.' + ext_name
    name = base
    if tag == '':
        name = name.split('-')[0]
    elif tag is not None:
        name = '%s-%s' % (name, tag)
    if ext_name != '':
        name = '%s%s' % (name, ext_name)
    return join_path(dir_name, name)


def match_color(args):
    from skimage.io import imread, imsave
    from skimage.exposure import match_histograms

    reference = imread(args.content)
    image = imread(args.style)

    matched = match_histograms(image, reference, multichannel=True)
    hm_name = make_filepath(args.style, tag='hm')
    print(f'match color to {hm_name}')
    imsave(hm_name, matched)
    args.style = hm_name


parser = argparse.ArgumentParser(description='WCT Pytorch')
parser.add_argument('--contentPath', default='images/content', help='path to train')
parser.add_argument('--stylePath', default='images/style', help='path to train')
parser.add_argument('--content', default=None, help='')
parser.add_argument('--style', default=None, help='')
parser.add_argument('--workers', default=2, type=int, metavar='N', help='number of data loading workers (default: 4)')
parser.add_argument('--encoder', default='models/vgg19_normalized.pth.tar', help='Path to the VGG conv1_1')
parser.add_argument('--decoder5', default='models/vgg19_normalized_decoder5.pth.tar', help='Path to the decoder5')
parser.add_argument('--decoder4', default='models/vgg19_normalized_decoder4.pth.tar', help='Path to the decoder4')
parser.add_argument('--decoder3', default='models/vgg19_normalized_decoder3.pth.tar', help='Path to the decoder3')
parser.add_argument('--decoder2', default='models/vgg19_normalized_decoder2.pth.tar', help='Path to the decoder2')
parser.add_argument('--decoder1', default='models/vgg19_normalized_decoder1.pth.tar', help='Path to the decoder1')
parser.add_argument('--cuda', default=True, help='enables cuda')
parser.add_argument('--gray', default=False, action='store_const', const=True, help='')
parser.add_argument('--match', default=False, action='store_const', const=True, help='')
parser.add_argument('--transform-method', choices=['original', 'closed-form'], default='original',
                    help=('How to whiten and color the features. "original" for the formulation of Li et al. ( https://arxiv.org/abs/1705.08086 )  '
                          'or "closed-form" for method of Lu et al. ( https://arxiv.org/abs/1906.00668 '))
parser.add_argument('--batch_size', type=int, default=1, help='batch size')
parser.add_argument('--fineSize', type=int, default=0, help='resize image to fineSize x fineSize,leave it to 0 if not resize')
parser.add_argument('--outf', default='output/', help='folder to output images')
parser.add_argument('--targets', default=[5, 4, 3, 2, 1], nargs='+', help='which layers to stylize at. Order matters!')
parser.add_argument('--gamma', type=float, default=0.8, help='hyperparameter to blend original content feature and colorized features. See Wynen et al. 2018 eq. (3)')
parser.add_argument('--delta', type=float, default=0.9, help='hyperparameter to blend wct features from current input and original input. See Wynen et al. 2018 eq. (3)')
parser.add_argument('--gpu', type=int, default=0, help="which gpu to run on.  default is 0")

args = parser.parse_args()
#pprint.pprint(args.__dict__, indent=2)

try:
    os.makedirs(args.outf)
except OSError:
    pass

if args.match:
    match_color(args)

# Data loading code
if args.gray:
    dataset = DatasetOne(args.content, args.style, args.fineSize, loader=gray_loader)
else:
    dataset = DatasetOne(args.content, args.style, args.fineSize)

loader = torch.utils.data.DataLoader(dataset=dataset, batch_size=1, shuffle=False)


def styleTransfer(wct, targets, contentImg, styleImg, imname, gamma, delta, outf, transform_method):
    current_result = contentImg
    eIorigs = [f.cpu().squeeze(0) for f in wct.encoder(contentImg, targets)]
    eIss = [f.cpu().squeeze(0) for f in wct.encoder(styleImg, targets)]

    for i, (target, eIorig, eIs) in enumerate(zip(targets, eIorigs, eIss)):
        print(f'    stylizing at {target}')

        if i == 0:
            eIlast = eIorig
        else:
            eIlast = wct.encoder(current_result, target).cpu().squeeze(0)

        CsIlast = wct.transform(eIlast, eIs, transform_method).float()
        CsIorig = wct.transform(eIorig, eIs, transform_method).float()

        decoder_input = (gamma * (delta * CsIlast + (1 - delta) * CsIorig) + (1 - gamma) * eIorig)
        decoder_input = decoder_input.unsqueeze(0).to(next(wct.parameters()).device)

        decoder = wct.decoders[target]
        current_result = decoder(decoder_input)

    # save_image has this wired design to pad images with 4 pixels at default.
    vutils.save_image(current_result.cpu().float(), os.path.join(outf, imname))
    return current_result


def main():
    wct = WCT(args)
    if (args.cuda):
        wct.cuda(args.gpu)

    avgTime = 0
    for i, (contentImg, styleImg, imname) in enumerate(loader):
        if (args.cuda):
            contentImg = contentImg.cuda(args.gpu)
            styleImg = styleImg.cuda(args.gpu)
        imname = imname[0]
        print(f'Transferring {imname}')
        if (args.cuda):
            contentImg = contentImg.cuda(args.gpu)
            styleImg = styleImg.cuda(args.gpu)
        start_time = time.time()
        # WCT Style Transfer
        targets = [f'relu{t}_1' for t in args.targets]
        styleTransfer(wct, targets, contentImg, styleImg, imname,
                      args.gamma, args.delta, args.outf, args.transform_method)
        end_time = time.time()
        print(' Elapsed time is: %f' % (end_time - start_time))
        avgTime += (end_time - start_time)

    print('Processed %d images. Averaged time is %f' % ((i + 1), avgTime / (i + 1)))


if __name__ == '__main__':
    with torch.no_grad():
        main()
