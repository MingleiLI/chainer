#!/usr/bin/env python
import numpy
import six.moves.cPickle as pickle

n_result = 5  # number of search result to show


with open('model.pickle', 'rb') as f:
    model, index2word, word2index = pickle.load(f)

w = model.embed.W
s = numpy.sqrt((w * w).sum(1))
w /= s.reshape((s.shape[0], 1))  # normalize

try:
    while True:
        q = raw_input('>> ')
        if q not in word2index:
            print('"{0}" is not found'.format(q))
            continue
        v = w[word2index[q]]
        similarity = w.dot(v)
        print('query: {}'.format(q))
        count = 0
        for i in (-similarity).argsort():
            if numpy.isnan(similarity[i]):
                continue
            print('{0}: {1}'.format(index2word[i], similarity[i]))
            count += 1
            if count == n_result:
                break

except EOFError:
    pass
