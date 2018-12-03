"""
Preprocess a raw json dataset into hdf5 and json files for use in misc/DataLoader.lua

Input: refer loader
Output: json file has 
- refs: 	  [{ref_id, ann_id, box, image_id, split, category_id, sent_ids}]
- images: 	  [{image_id, ref_ids, file_name, width, height, h5_id}]
- anns: 	  [{ann_id, category_id, image_id, box, h5_id}]
- sentences:  [{sent_id, tokens, sent}]
- ix_to_word: {ix: word}
- word_to_ix: {word: ix}

Output: hdf5 file has
/labels is (M, max_length) uint32 array of encoded labels, zeros padded
"""
import os
import sys
import json
import config
import string
# non-standard dependencies:
import h5py
import numpy as np
from scipy.misc import imread, imresize
import matplotlib.pyplot as plt
# add root path
import os.path as osp
ROOT_DIR = '.'

def build_vocab(refer, params):
	"""
  	remove bad words, and return final sentences (sent_id --> final)
  	"""
	count_thr = params['word_count_threshold']
	sentToTokens = refer.sentToTokens

	# count up the number of words
	word2count = {}
	for sent_id, tokens in sentToTokens.items():
		for wd in tokens:
			word2count[wd] = word2count.get(wd, 0) + 1

	# print some stats
	total_words = sum([w[1] for w in word2count.items()])
	print ('total words: %s' % total_words)
	bad_words = [w for w, n in word2count.items() if n <= count_thr]
	vocab = [w for w, n in word2count.items() if n > count_thr]
	bad_count = sum([word2count[w] for w in bad_words])
	print ('number of good words: %d' % len(vocab))
	print ('number of bad words: %d/%d = %.2f%%' % (len(bad_words), len(word2count), len(bad_words)*100.0/len(word2count)))
	print ('number of UNKs in sentences: %d/%d = %.2f%%' % (bad_count, total_words, bad_count*100.0/total_words))

	# add UNK
	if bad_count > 0:
		vocab.append('UNK')

	# lets now produce final tokens
	sentToFinal = {}
	for sent_id, tokens in sentToTokens.items():
		final = [w if word2count[w] > count_thr else 'UNK' for w in tokens]
		sentToFinal[sent_id] = final
	
	return vocab, sentToFinal 


def check_sentLength(sentToFinal):
	sent_lengths = {}
	for sent_id, tokens in sentToFinal.items():
		nw = len(tokens)
		sent_lengths[nw] = sent_lengths.get(nw, 0) + 1
	max_len = max(sent_lengths.keys())
	print ('max length of sentence in raw data is %d' % max_len)
	print ('sentence length distribution (count, number of words):')
	sum_len = sum(sent_lengths.values())
	acc = 0  # accumulative distribution
	for i in range(max_len+1):
		acc += sent_lengths.get(i, 0)
		print ('%2d: %10d %.3f%% %.3f%%' % (i, sent_lengths.get(i, 0), sent_lengths.get(i, 0)*100.0/sum_len, acc*100.0/sum_len))


def encode_captions(sentences, wtoi, params):
	"""
	seqz is zero-padded in the end, for language model learning.
	zseq is zero-padded in the begining, for embedding model learning.
	"""
	max_length = params['max_length']
	if max_length == None:
		if params['dataset'] in ['refcoco', 'refclef', 'refcoco+']:
			max_length = 10
		elif params['dataset'] in ['refcocog','refgta']:
			max_length = 20
	# encode seqz and zseq
	M = len(sentences)
	seqz_L = np.zeros((M, max_length), dtype='uint32')
	zseq_L = np.zeros((M, max_length), dtype='uint32')

	for sent in sentences:
		h5_id = sent['h5_id']
		# encode seqz
		tokens = sent['tokens']
		start_ix = max(max_length-len(tokens), 0)
		for j, w in enumerate(tokens):
			if j < max_length:
				zseq_L[h5_id, start_ix+j] = wtoi[w]
				seqz_L[h5_id, j] = wtoi[w]
	# return 
	return seqz_L, zseq_L


def check_encoded_labels(sentences, seqz_L, zseq_L, itow):
	for sent in sentences:
		# print gd-truth
		print('gd : %s' % (' '.join(sent['tokens'])))
		# decode seqz and zseq 
		h5_id = sent['h5_id']
		seqz = seqz_L[h5_id].tolist()
		sent = ' '.join([itow[w] for w in seqz if w != 0])
		print('seqz: %s' % sent)
		zseq = zseq_L[h5_id].tolist()
		sent = ' '.join([itow[w] for w in zseq if w != 0])
		print('zseq: %s' % sent)
		print(seqz)
		print(zseq)
		print('\n')


def prepare_json(refer, sentToFinal, params):
	# prepare refs = [{ref_id, ann_id, image_id, split, category_id, sent_ids}]
	refs = []
	for ref_id, ref in refer.Refs.items():
		box = refer.refToAnn[ref_id]['bbox']
		refs += [ {'ref_id': ref_id, 'split': ref['split'], 'category_id': ref['category_id'], 'ann_id': ref['ann_id'],
		'sent_ids': ref['sent_ids'], 'box': box, 'image_id': ref['image_id']} ]
	print ('There in all %s refs.' % len(refs))

	# prepare images = [{'image_id', 'width', 'height', 'file_name', 'ref_ids', 'ann_ids', 'h5_id'}]
	images = []
	h5_id = 0
	for image_id, image in refer.Imgs.items():
		width = image['width']
		height = image['height']
		file_name = image['file_name']
		ref_ids = [ref['ref_id'] for ref in refer.imgToRefs[image_id]]
		ann_ids = [ann['id'] for ann in refer.imgToAnns[image_id]]
		images += [ {'image_id': image_id, 'height': height, 'width': width, 'file_name': file_name, 'ref_ids': ref_ids, 'ann_ids': ann_ids, 'h5_id': h5_id} ]
		h5_id += 1  
	print ('There are in all %d images.' % h5_id)

	# prepare anns appeared in images, anns = [{ann_id, category_id, image_id, box, h5_id}]
	anns = []
	h5_id = 0
	for image_id in refer.Imgs:
		ann_ids = [ann['id'] for ann in refer.imgToAnns[image_id]]
		for ann_id in ann_ids:
			ann = refer.Anns[ann_id]
			anns += [{'ann_id': ann_id, 'category_id': ann['category_id'], 'box': ann['bbox'], 'image_id': image_id, 'h5_id': h5_id}]
			h5_id += 1  
	print ('There are in all %d anns within the %d images.' % (h5_id, len(images)))

	# prepare sentences = [{sent_id, tokens}]
	sentences = []
	h5_id = 0
	for sent_id, tokens in sentToFinal.items():
		sentences += [{'sent_id': sent_id, 'tokens': tokens, 'sent':  ' '.join(tokens), 'h5_id': h5_id}]
		h5_id = h5_id + 1  
	print ('There are in all %d sentences to be written into hdf5 file.' % h5_id)

	# return
	return refs, images, anns, sentences


def main(params):

	# dataset_splitBy
	data_root, dataset, splitBy = params['data_root'], params['dataset'], params['splitBy']
	target_save_dir = osp.join(params['save_dir']+'/prepro', dataset+'_'+splitBy)

	if params['old']:
		params['data_json'] = 'old'+params['data_json']
		params['data_h5'] = 'old'+params['data_h5']
	if 'refcoco' in dataset:
		image_root = params['coco_image_root']
	else:
		image_root = params['gta_image_root']
		
	# mkdir and write json file
	if not osp.isdir(target_save_dir):
		os.makedirs(target_save_dir)

	# load refer
	sys.path.insert(0, osp.join(ROOT_DIR, 'pyutils/refer2'))
	from refer import REFER
	refer = REFER(data_root, image_root, dataset, splitBy, old_version=params['old'])
	# create vocab
	vocab, sentToFinal = build_vocab(refer, params)
	itow = {i+1: w for i, w in enumerate(vocab)}# 0 : eos, len(itow)+1 : sos
	wtoi = {w: i+1 for i, w in enumerate(vocab)} 
	# check sentence length
	check_sentLength(sentToFinal)

	# prepare refs, images, anns, sentences
	# and write json
	refs, images, anns, sentences = prepare_json(refer, sentToFinal, params)
	json.dump({'refs': refs, 
			   'images': images, 
			   'anns': anns, 
			   'sentences': sentences, 
			   'ix_to_word': itow, 
			   'word_to_ix': wtoi,
			   'ix_to_cat': refer.Cats,
			   'sents_info':refer.Sents
			}, 
		open(osp.join(target_save_dir, params['data_json']), 'w'))
	print ('%s written.' % osp.join(target_save_dir, params['data_json']))

	# write h5 file which contains /sentences
	f = h5py.File(osp.join(target_save_dir, params['data_h5']), 'w')
	seqz_L, zseq_L = encode_captions(sentences, wtoi, params)
	f.create_dataset("seqz_labels", dtype='uint32', data=seqz_L)
	f.create_dataset("zseq_labels", dtype='uint32', data=zseq_L)
	f.close()
	print ('%s writtern.' % osp.join(target_save_dir, params['data_h5']))
	# check_encoded_labels(sentences, seqz_L, zseq_L, itow)


if __name__ == '__main__':

	args = config.parse_opt()
	params = vars(args) # convert to ordinary dict
	print ('parsed input parameters:')
	print (json.dumps(params, indent = 2))

	# call main
	main(params)






